"""Root worker for catalog refresh, backup retry and verified live mail-data recovery."""

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from helper.backup_control import BackupControl
from helper.maddy import DockerMaddy
from helper.recovery import require_qualified
from helper.recovery_records import RecoveryRecords
from host.backup_operations import BACKUP_LOCK, BackupOperations
from host.context import HostContext
from host.daily_retention import RetentionLedger
from host.docker import Docker, DockerSnapshots
from host.locks import HostLock, LockPolicy
from host.mailbox_store import body_path, connect, validate
from host.mailbox_transaction import mailbox_transaction
from host.preflight import provision_lock
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from shared.errors import ServiceError
from shared.files import sync_directory
from shared.validation import address

ROOT = Path("/var/lib/mail-admin-helper")
STAGING = Path("/var/lib/mail-admin-backup/restore-ui")


class BackupWorker:
    def __init__(self, context: HostContext, root: Path = ROOT, staging: Path = STAGING) -> None:
        self.context, self.root, self.staging = context, root, staging
        self.control = BackupControl(root)
        provision_lock(root / "backup-worker.lock")
        self.staging.mkdir(mode=0o700, exist_ok=True)
        if self.staging.is_symlink() or self.staging.stat().st_uid != 0:
            raise ServiceError("unsafe_restore_parent", 503)
        self.repository = ResticRepository(ResticProcess(context.deployment), time.time)

    def catalog(self) -> None:
        now = time.time()
        try:
            with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
                snapshots = self.repository.snapshots()
            ledger = RetentionLedger(self.repository).record
            with self.control.connect() as connection:
                active_pins = set()
                for job in connection.execute(
                    "SELECT snapshot,result FROM jobs WHERE state IN "
                    "('queued','running','reconciliation_required')"
                ):
                    active_pins.add(job["snapshot"])
                    active_pins.add(json.loads(job["result"]).get("safety_repository_snapshot", ""))
                connection.execute("DELETE FROM snapshot_details")
                connection.executemany(
                    "INSERT INTO snapshot_details VALUES(?,?)",
                    [
                        (
                            row.identifier,
                            json.dumps(
                                {
                                    "verification": ledger["verification"].get(row.identifier),
                                    "temporary_pin": row.identifier in active_pins
                                    or row.identifier
                                    in ledger.get("cleanup", {}).get("temporary_pins", []),
                                    "cleanup": ledger.get("cleanup", {}),
                                }
                            ),
                        )
                        for row in snapshots
                    ],
                )
                connection.execute("DELETE FROM snapshots")
                connection.executemany(
                    "INSERT INTO snapshots VALUES(?,?,?)",
                    [(row.identifier, row.cutoff, now) for row in snapshots],
                )
                connection.execute("INSERT OR REPLACE INTO observations VALUES(1,?,?)", (now, ""))
        except (OSError, ServiceError) as error:
            with self.control.connect() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO observations VALUES(1,?,?)",
                    (
                        now,
                        error.code
                        if isinstance(error, ServiceError)
                        else "backup_catalog_unavailable",
                    ),
                )
            raise

    def run(self) -> None:
        with HostLock(self.root / "backup-worker.lock", LockPolicy(0.1)):
            self.cleanup_staging()
            with self.control.connect() as connection:
                # The exclusive worker lock proves previous owners have exited.
                connection.execute(
                    "UPDATE jobs SET state='reconciliation_required',error='worker_interrupted' "
                    "WHERE state='running'"
                )
                row = connection.execute(
                    "SELECT id FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1"
                ).fetchone()
                observation = connection.execute(
                    "SELECT observed FROM observations WHERE id=1"
                ).fetchone()
            if row is None:
                if observation is None or time.time() - observation[0] >= 300:
                    self.catalog()
                return
            job = self.control.job(row["id"], private=True)
            self.control.update(job["id"], "running", "starting", time.time())
            try:
                result = self.perform(job)
                self.control.update(job["id"], "complete", "complete", time.time(), result=result)
                if job["kind"] == "restore":
                    self.finish_retention(job, result)
            except (OSError, ValueError, sqlite3.Error, ServiceError) as error:
                current = self.control.job(job["id"])
                uncertain = current["phase"] in {
                    "backup_capture",
                    "live_import",
                    "resuming_mail",
                    "post_restore_backup",
                }
                self.control.update(
                    job["id"],
                    "reconciliation_required" if uncertain else "failed",
                    current["phase"],
                    time.time(),
                    error.code if isinstance(error, ServiceError) else "backup_operation_failed",
                )

    def finish_retention(self, job: dict[str, Any], result: dict[str, Any]) -> None:
        from host.repository_backup import RepositoryBackup
        from shared.files import protected_json

        try:
            backup = self.context.backup()
            operations = BackupOperations(backup, self.context.deployment)
            with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
                if isinstance(backup, RepositoryBackup):
                    backup.prune(operations._protected(protected_json(backup.paths.manifest)))
            self.catalog()
        except (OSError, ServiceError):
            result["cleanup"] = {"state": "failed", "error": "retention_cleanup_failed"}
            self.control.update(job["id"], "complete", "complete", time.time(), result=result)

    def cleanup_staging(self) -> None:
        """Verified extraction is private temporary state, retained at most one day."""
        with self.control.connect() as connection:
            protected = {
                json.loads(row[0])["previous"]
                for row in connection.execute(
                    "SELECT payload FROM jobs WHERE kind='restore' AND state IN "
                    "('queued','running','reconciliation_required')"
                )
            }
            expired = connection.execute(
                "SELECT id,state FROM jobs WHERE kind='preflight' AND created<? "
                "AND state NOT IN ('queued','running','reconciliation_required')",
                (time.time() - 86400,),
            ).fetchall()
        for row in expired:
            if row["id"] in protected:
                continue
            path = self.staging / str(uuid.UUID(row["id"]))
            if path.is_symlink():
                raise ServiceError("unsafe_restore_staging", 503)
            if path.exists():
                shutil.rmtree(path)
                sync_directory(self.staging)
            if row["state"] == "complete":
                self.control.update(
                    row["id"], "expired", "expired", time.time(), "backup_preflight_expired"
                )

    def phase(self, job: dict[str, Any], name: str) -> None:
        self.control.update(job["id"], "running", name, time.time())

    def perform(self, job: dict[str, Any]) -> dict[str, Any]:
        kind = job["kind"]
        if kind == "refresh":
            self.catalog()
            return {"refreshed": True}
        if kind == "backup":
            self.phase(job, "backup_capture")
            manifest = BackupOperations(self.context.backup(), self.context.deployment).run()
            self.phase(job, "catalog_refresh")
            try:
                self.catalog()
            except (OSError, ServiceError):
                # Publication is already verified; an unavailable catalog must not
                # turn successful creation into a failed backup or invite a retry.
                manifest["cleanup"] = {
                    "state": "failed",
                    "error": "cleanup_or_catalog_requires_check",
                }
            return {
                "reference": manifest["archive_reference"]
                if "archive_reference" in manifest
                else manifest.get("reference", ""),
                "verified": True,
                "cleanup": manifest.get("cleanup", {}),
            }
        if kind == "preflight":
            return self.preflight(job)
        if kind == "reconcile":
            return self.reconcile(job)
        if kind == "restore":
            return self.restore(job)
        raise ServiceError("invalid_backup_operation", 400)

    def preflight(self, job: dict[str, Any]) -> dict[str, Any]:
        target = self.staging / job["id"]
        if target.exists():
            raise ServiceError("restore_target_exists", 409)
        # Repository-wide maximum bounds extraction before starting any mail interruption.
        maximum = self.repository.options.max_archive_bytes
        if shutil.disk_usage(self.staging).free < maximum * 2 + 1024**3:
            raise ServiceError("insufficient_restore_space", 409)
        self.phase(job, "verifying_snapshot")
        with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
            self.repository.restore(self.repository.reference(job["snapshot"]), target)
        self.phase(job, "validating_mail_data")
        with connect(target / "maddy/imapsql.db") as connection:
            validate(connection)
            emails = [
                address(row[0])
                for row in connection.execute("SELECT username FROM users ORDER BY username")
            ]
            keys = [row[0] for row in connection.execute("SELECT id FROM extKeys")]
            sizes = sum(body_path(target / "maddy/messages", key).stat().st_size for key in keys)
        if (
            len(emails) > 32
            or len(keys) > 2000
            or sizes + (target / "maddy/imapsql.db").stat().st_size > 32 * 1024**2
        ):
            raise ServiceError("restore_maintenance_budget_exceeded", 409)
        with self.context.coordination.consistent():
            inventory = DockerMaddy(self.context.deployment).inventories()
            if set(inventory["credentials"]) != set(inventory["storage"]):
                raise ServiceError("partial_account_requires_operator_repair", 409)
        missing = sorted(set(emails) - set(inventory["storage"]))
        return {
            "mailboxes": emails,
            "missing": missing,
            "bytes": sizes,
            "verified": True,
            "mode": "merge",
            "snapshot": job["snapshot"],
        }

    def restore(self, job: dict[str, Any]) -> dict[str, Any]:
        require_qualified(self.root, 0)
        prepared = self.control.job(job["payload"]["previous"])
        if prepared["state"] != "complete" or prepared["snapshot"] != job["snapshot"]:
            raise ServiceError("backup_preflight_required", 409)
        target = self.staging / prepared["id"]
        self.phase(job, "rechecking_snapshot")
        self.repository.verify_restored(target)
        self.phase(job, "safety_backup")
        # Safety capture is verified and published before a live-data mutation.
        safety = BackupOperations(self.context.backup(), self.context.deployment).run()
        safety_id = safety.get(
            "repository_snapshot", safety.get("publication", {}).get("repository_snapshot", "")
        )
        self.control.update(
            job["id"],
            "running",
            "safety_backup_verified",
            time.time(),
            result={"safety_repository_snapshot": safety_id},
        )
        self.phase(job, "waiting_for_mail_lock")
        with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
            with HostLock(self.root / "recovery-worker.lock", LockPolicy(1)):
                if RecoveryRecords(self.root / "recovery.db").busy():
                    raise ServiceError("recovery_operation_in_progress", 409)
                with self.context.coordination.consistent():
                    result = self.import_live(job, target, safety)
        self.phase(job, "post_restore_backup")
        post = BackupOperations(self.context.backup(), self.context.deployment).run()
        result["post_restore_verified"] = True
        result["post_restore_snapshot"] = post["snapshot_id"]
        return result

    def import_live(
        self, job: dict[str, Any], target: Path, safety: dict[str, Any]
    ) -> dict[str, Any]:
        from host.backup_merge import merge_rows

        data = Path(self.context.deployment["maddy_data_directory"])
        bodies = data / "messages"
        maddy = DockerMaddy(self.context.deployment)
        maddy.validate()
        inventory = maddy.inventories()
        missing = set(self.control.job(job["payload"]["previous"])["result"]["mailboxes"]) - set(
            inventory["storage"]
        )
        if set(inventory["credentials"]) != set(inventory["storage"]) or missing != set(
            job["payload"]["password_hashes"]
        ):
            raise ServiceError("mailbox_inventory_changed", 409)
        journal = self.context.coordination.journal
        journal.synchronize(inventory, time.time())
        with connect(target / "maddy/imapsql.db") as source:
            original_keys = [row[0] for row in source.execute("SELECT id FROM extKeys")]
        keys = {key: str(uuid.uuid5(uuid.UUID(job["id"]), key)) for key in original_keys}
        size = sum(
            body_path(target / "maddy/messages", key).stat().st_size for key in original_keys
        )
        if shutil.disk_usage(bodies).free < size * 8 + 1024**3:
            raise ServiceError("insufficient_restore_space", 409)
        writers = DockerSnapshots(
            (Docker(timeout=30), maddy, self.context.generations), startup_timeout=30
        )
        with connect(data / "imapsql.db") as live:
            live_keys = [row[0] for row in live.execute("SELECT id FROM extKeys")]
        live_size = sum(body_path(bodies, key).stat().st_size for key in live_keys)
        database_bytes = sum(
            path.stat().st_size
            for path in (
                data / "imapsql.db",
                data / "credentials.db",
                journal.path,
                self.control.path,
                Path(self.context.deployment["backup_sources"]["dashboard"]) / "admin_data.db",
            )
        )
        if len(live_keys) + len(keys) > 2000 or live_size + size + database_bytes > 32 * 1024**2:
            raise ServiceError("restore_maintenance_budget_exceeded", 409)
        signal.alarm(480)
        self.phase(job, "live_import")
        try:
            writers.pause(["maddy"])
            metadata = bodies.stat()
            with connect(data / "imapsql.db") as connection:
                validate(connection)
                for new in keys.values():
                    if (
                        (bodies / new).exists()
                        or (bodies / new).is_symlink()
                        or connection.execute(
                            "SELECT 1 FROM extKeys WHERE id=?",
                            (new,),
                        ).fetchone()
                    ):
                        raise ServiceError("mailbox_import_body_conflict", 409)
                with self.control.connect() as ledger:
                    ledger.execute(
                        "INSERT INTO backup_plans VALUES(?,?)",
                        (job["id"], json.dumps(list(keys.values()))),
                    )
                for old, new in keys.items():
                    destination = bodies / new
                    if (
                        destination.exists()
                        or destination.is_symlink()
                        or connection.execute("SELECT 1 FROM extKeys WHERE id=?", (new,)).fetchone()
                    ):
                        raise ServiceError("mailbox_import_body_conflict", 409)
                    with (
                        body_path(target / "maddy/messages", old).open("rb") as reader,
                        destination.open("xb") as writer,
                    ):
                        shutil.copyfileobj(reader, writer)
                        writer.flush()
                        os.fsync(writer.fileno())
                    os.chown(destination, metadata.st_uid, metadata.st_gid)
                    destination.chmod(0o600)
                    with (
                        destination.open("rb") as reader,
                        body_path(target / "maddy/messages", old).open("rb") as original,
                    ):
                        if (
                            hashlib.file_digest(reader, "sha256").digest()
                            != hashlib.file_digest(original, "sha256").digest()
                        ):
                            raise ServiceError("mailbox_import_body_verification_failed", 503)
                        os.fsync(reader.fileno())
            sync_directory(bodies)
            paths = {
                "main": data / "imapsql.db",
                "credentials": data / "credentials.db",
                "dashboard": Path(self.context.deployment["backup_sources"]["dashboard"])
                / "admin_data.db",
                "incarnations": journal.path,
                "ledger": self.control.path,
            }
            with mailbox_transaction(paths) as connection:
                count = merge_rows(
                    connection, bodies, target / "maddy/imapsql.db", target / "maddy/messages", keys
                )
                with sqlite3.connect(
                    (target / "dashboard/admin_data.db").as_uri() + "?mode=ro", uri=True
                ) as source_metadata:
                    source_metadata.row_factory = sqlite3.Row
                    for email in sorted(missing):
                        row = source_metadata.execute(
                            "SELECT display_name,notes,created FROM mailboxes WHERE email=?",
                            (email,),
                        ).fetchone()
                        if row is None:
                            raise ServiceError("mailbox_metadata_missing", 409)
                        connection.execute(
                            "INSERT INTO credentials.passwords(key,value) VALUES(?,?)",
                            (email, job["payload"]["password_hashes"][email]),
                        )
                        connection.execute(
                            "INSERT INTO dashboard.mailboxes"
                            "(email,display_name,notes,created,updated,status) "
                            "VALUES(?,?,?,?,?,'ready') ON CONFLICT(email) DO UPDATE SET "
                            "display_name=excluded.display_name,notes=excluded.notes,"
                            "created=excluded.created,updated=excluded.updated,status='ready'",
                            (email, row["display_name"], row["notes"], row["created"], time.time()),
                        )
                        connection.execute(
                            "INSERT INTO incarnations.incarnations VALUES(?,?,?,1) ON "
                            "CONFLICT(email) DO UPDATE SET "
                            "generation=excluded.generation,created=excluded.created,present=1",
                            (email, job["id"], time.time()),
                        )
                connection.execute(
                    "INSERT INTO ledger.backup_imports VALUES(?,?)", (job["id"], time.time())
                )
            # Only unreferenced files owned by this exact operation are disposable.
            with connect(data / "imapsql.db") as connection:
                for key in keys.values():
                    if not connection.execute(
                        "SELECT 1 FROM extKeys WHERE id=?", (key,)
                    ).fetchone():
                        (bodies / key).unlink()
            sync_directory(bodies)
        finally:
            try:
                self.phase(job, "resuming_mail")
            finally:
                try:
                    writers.resume(["maddy"])
                finally:
                    signal.alarm(0)
        return {
            "messages_added": count,
            "recreated": sorted(missing),
            "safety_snapshot": safety.get("snapshot_id"),
            "mode": "merge",
        }

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        original = self.control.job(job["payload"]["previous"], private=True)
        with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
            self.repository.process.reconcile()
            with HostLock(self.root / "recovery-worker.lock", LockPolicy(1)):
                with self.context.coordination.consistent():
                    maddy = DockerMaddy(self.context.deployment)
                    writers = DockerSnapshots(
                        (Docker(timeout=30), maddy, self.context.generations), startup_timeout=30
                    )
                    state = self.context.generations.read()
                    for name, consumer in state["consumers"].items():
                        if consumer.get("reason") == "backup_capture":
                            if name == "maddy":
                                observed = self.context.docker.inspect(name)
                                if (
                                    observed["Image"]
                                    != self.context.deployment["inputs"]["maddy_image_id"]
                                ):
                                    raise ServiceError("maddy_identity_mismatch", 503)
                                mounts = {
                                    entry["Destination"]: entry["Source"]
                                    for entry in observed["Mounts"]
                                }
                                if mounts != maddy.expected_mounts():
                                    raise ServiceError("maddy_mount_mismatch", 503)
                            writers._start_existing(name)
                            writers._verify_resumed(name, consumer["served_sha256"])
                            consumer.update(intent="expected_running", state="active")
                            consumer.pop("reason", None)
                    self.context.generations.write(state)
                    with self.control.connect() as connection:
                        committed = (
                            connection.execute(
                                "SELECT 1 FROM backup_imports WHERE id=?", (original["id"],)
                            ).fetchone()
                            is not None
                        )
                    if original["kind"] == "restore" and not committed:
                        data = Path(self.context.deployment["maddy_data_directory"])
                        with self.control.connect() as ledger:
                            plan = ledger.execute(
                                "SELECT keys FROM backup_plans WHERE id=?", (original["id"],)
                            ).fetchone()
                        keys = json.loads(plan[0]) if plan else []
                        with connect(data / "imapsql.db") as live:
                            for key in keys:
                                if live.execute(
                                    "SELECT 1 FROM extKeys WHERE id=?", (key,)
                                ).fetchone():
                                    raise ServiceError("backup_restore_checkpoint_conflict", 409)
                                path = data / "messages" / key
                                if path.is_symlink():
                                    raise ServiceError("mailbox_import_body_conflict", 409)
                                path.unlink(missing_ok=True)
                        sync_directory(data / "messages")
        if committed:
            self.phase(job, "post_restore_backup")
            BackupOperations(self.context.backup(), self.context.deployment).run()
        self.control.update(
            original["id"],
            "complete" if committed else "failed",
            "reconciled",
            time.time(),
            result={"committed": committed, "mutation_replayed": False},
        )
        return {"operation": original["id"], "committed": committed, "mutation_replayed": False}


def main() -> None:
    from host.mailbox_recovery import interrupted

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    BackupWorker(HostContext.load()).run()


if __name__ == "__main__":
    main()
