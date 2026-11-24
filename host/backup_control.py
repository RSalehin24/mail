"""Verified live recovery of mail data from a backup, run inside the owner's VM command.

Nothing here waits on a queue: `host.restore_backup` records a job, runs it in its own process
and reports the outcome. The job records only make an interrupted run recoverable.
"""

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from helper.backup_control import BackupControl
from helper.maddy import DockerMaddy, cli_succeeded
from helper.recovery_records import RecoveryRecords
from helper.state_database import STATE_FILE
from host.backup_catalog import ROOT, WORKER_LOCK, BackupCatalog
from host.backup_operations import BACKUP_LOCK, BackupOperations
from host.context import HostContext
from host.docker import Docker, DockerSnapshots
from host.locks import HostLock, LockPolicy
from host.mailbox_store import body_path, connect, linked_body_keys, validate
from host.mailbox_transaction import attached_transaction
from host.preflight import provision_lock
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from shared.errors import ServiceError
from shared.files import sync_directory
from shared.validation import address

STAGING = Path("/var/lib/mail-admin-backup/restore-ui")
WORKER_LOCK_WAIT_SECONDS = 5
LIVE_WRITE_WAIT_SECONDS = 5.0
# After these phases the live data may already have changed, so a failure needs reconciling.
UNCERTAIN_PHASES = {
    "backup_capture",
    "live_import",
    "creating_logins",
    "post_restore_backup",
}


def login_plan(emails: set[str], inventory: dict[str, list[str]]) -> tuple[set[str], set[str]]:
    """Return the backed-up mailboxes to import and those that need a new login password."""
    storage, credentials = set(inventory["storage"]), set(inventory["credentials"])
    if (storage ^ credentials) - emails:
        raise ServiceError("partial_account_requires_operator_repair", 409)
    return emails - storage, emails - credentials


class BackupWorker:
    def __init__(self, context: HostContext, root: Path = ROOT, staging: Path = STAGING) -> None:
        self.context, self.root, self.staging = context, root, staging
        self.control = BackupControl(root)
        provision_lock(root / WORKER_LOCK)
        self.staging.mkdir(mode=0o700, exist_ok=True)
        if self.staging.is_symlink() or self.staging.stat().st_uid != 0:
            raise ServiceError("unsafe_restore_parent", 503)
        self.repository = ResticRepository(ResticProcess(context.deployment), time.time)
        self.catalog = BackupCatalog(context, self.control, self.repository)

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the worker lock: no scheduled backup or second restore can run alongside."""
        with HostLock(self.root / WORKER_LOCK, LockPolicy(WORKER_LOCK_WAIT_SECONDS)):
            self.control.interrupt_running()
            self.cleanup_staging()
            yield

    def submit(self, kind: str, snapshot: str = "", previous: str = "") -> str:
        identifier = str(uuid.uuid4())
        self.control.begin((identifier, kind, snapshot, previous), time.time())
        return identifier

    def process(
        self, identifier: str, passwords: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """Run one recorded job to its end; the passwords stay in memory and are never stored."""
        job = self.control.job(identifier, private=True)
        try:
            result = self.perform(job, passwords or {})
        except (OSError, ValueError, sqlite3.Error, ServiceError) as error:
            self.record_failure(job, error)
            raise
        self.control.update(identifier, "complete", "complete", time.time(), result=result)
        if job["kind"] == "restore":
            self.finish_retention(job, result)
        return self.control.job(identifier)

    def record_failure(self, job: dict[str, Any], error: Exception) -> None:
        current = self.control.job(job["id"])
        self.control.update(
            job["id"],
            "reconciliation_required" if current["phase"] in UNCERTAIN_PHASES else "failed",
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
            self.catalog.refresh()
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

    def perform(self, job: dict[str, Any], passwords: Mapping[str, str]) -> dict[str, Any]:
        kind = job["kind"]
        if kind == "preflight":
            return self.preflight(job)
        if kind == "reconcile":
            return self.reconcile(job)
        if kind == "restore":
            return self.restore(job, passwords)
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
            keys = linked_body_keys(connection)
            sizes = sum(body_path(target / "maddy/messages", key).stat().st_size for key in keys)
        if (
            len(emails) > 32
            or len(keys) > 2000
            or sizes + (target / "maddy/imapsql.db").stat().st_size > 32 * 1024**2
        ):
            raise ServiceError("restore_maintenance_budget_exceeded", 409)
        with self.context.coordination.consistent():
            inventory = DockerMaddy(self.context.deployment).inventories()
        missing, needs_login = login_plan(set(emails), inventory)
        return {
            "mailboxes": emails,
            "missing": sorted(missing),
            "needs_login": sorted(needs_login),
            "bytes": sizes,
            "verified": True,
            "mode": "merge",
            "snapshot": job["snapshot"],
        }

    def restore(self, job: dict[str, Any], passwords: Mapping[str, str]) -> dict[str, Any]:
        prepared = self.control.job(job["payload"]["previous"])
        if prepared["state"] != "complete" or prepared["snapshot"] != job["snapshot"]:
            raise ServiceError("backup_preflight_required", 409)
        if set(passwords) != set(prepared["result"]["needs_login"]):
            raise ServiceError("missing_mailbox_passwords_required", 400)
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
            if RecoveryRecords(self.root / STATE_FILE).busy():
                raise ServiceError("recovery_operation_in_progress", 409)
            with self.context.coordination.consistent():
                result = self.import_live(job, target, safety)
        self.phase(job, "creating_logins")
        self.create_logins(passwords)
        self.phase(job, "post_restore_backup")
        post = BackupOperations(self.context.backup(), self.context.deployment).run()
        result["post_restore_verified"] = True
        result["post_restore_snapshot"] = post["snapshot_id"]
        return result

    def create_logins(self, passwords: Mapping[str, str]) -> None:
        """Maddy hashes each password itself; a repeated run skips logins that already exist."""
        maddy = DockerMaddy(self.context.deployment)
        existing = set(maddy.inventories()["credentials"])
        for email in sorted(set(passwords) - existing):
            if not cli_succeeded(maddy.change(("creds", "create", email), passwords[email])):
                raise ServiceError("credentials_create_failed", 503)

    def live_data(self) -> Path:
        return Path(self.context.deployment["maddy_data_directory"])

    def dashboard_database(self) -> Path:
        return Path(self.context.deployment["backup_sources"]["dashboard"]) / "admin_data.db"

    def import_live(
        self, job: dict[str, Any], target: Path, safety: dict[str, Any]
    ) -> dict[str, Any]:
        data = self.live_data()
        maddy = DockerMaddy(self.context.deployment)
        maddy.validate()
        inventory = maddy.inventories()
        prepared = self.control.job(job["payload"]["previous"])["result"]
        missing, needs_login = login_plan(set(prepared["mailboxes"]), inventory)
        if (missing, needs_login) != (set(prepared["missing"]), set(prepared["needs_login"])):
            raise ServiceError("mailbox_inventory_changed", 409)
        self.context.coordination.journal.synchronize(inventory, time.time())
        with connect(target / "maddy/imapsql.db") as source:
            original_keys = linked_body_keys(source)
        keys = {key: str(uuid.uuid5(uuid.UUID(job["id"]), key)) for key in original_keys}
        size = sum(
            body_path(target / "maddy/messages", key).stat().st_size for key in original_keys
        )
        if shutil.disk_usage(data / "messages").free < size * 8 + 1024**3:
            raise ServiceError("insufficient_restore_space", 409)
        self.check_maintenance_budget(len(keys), size)
        # Maddy keeps running: the mail rows join its WAL database in one ordinary write
        # transaction, then the dashboard bookkeeping commits separately. A crash between the
        # two is settled by reconcile(), which recognizes the merge by its staged body keys.
        signal.alarm(480)
        self.phase(job, "live_import")
        try:
            self.stage_bodies(job["id"], target, keys)
            count = self.merge_live(target, keys)
            self.record_import(job, missing, target)
            self.remove_unreferenced(list(keys.values()))
        finally:
            signal.alarm(0)
        return {
            "messages_added": count,
            "recreated": sorted(missing),
            "safety_snapshot": safety.get("snapshot_id"),
            "mode": "merge",
        }

    def check_maintenance_budget(self, new_keys: int, new_size: int) -> None:
        data = self.live_data()
        with connect(data / "imapsql.db") as live:
            live_keys = linked_body_keys(live)
        live_size = sum(body_path(data / "messages", key).stat().st_size for key in live_keys)
        database_bytes = sum(
            path.stat().st_size
            for path in (
                data / "imapsql.db",
                data / "credentials.db",
                self.context.coordination.journal.path,
                self.control.path,
                self.dashboard_database(),
            )
        )
        if len(live_keys) + new_keys > 2000 or live_size + new_size + database_bytes > 32 * 1024**2:
            raise ServiceError("restore_maintenance_budget_exceeded", 409)

    @staticmethod
    def require_free_key(connection: sqlite3.Connection, bodies: Path, key: str) -> None:
        if (
            (bodies / key).exists()
            or (bodies / key).is_symlink()
            or connection.execute("SELECT 1 FROM extKeys WHERE id=?", (key,)).fetchone()
        ):
            raise ServiceError("mailbox_import_body_conflict", 409)

    def stage_bodies(self, job_id: str, target: Path, keys: dict[str, str]) -> None:
        """Copy the new body files durably; their planned names are recorded before any exists."""
        data = self.live_data()
        bodies = data / "messages"
        metadata = bodies.stat()
        with connect(data / "imapsql.db") as connection:
            validate(connection)
            for new in keys.values():
                self.require_free_key(connection, bodies, new)
            with self.control.connect() as ledger:
                ledger.execute(
                    "INSERT INTO backup_plans VALUES(?,?)",
                    (job_id, json.dumps(list(keys.values()))),
                )
            for old, new in keys.items():
                self.require_free_key(connection, bodies, new)
                self.copy_body(body_path(target / "maddy/messages", old), bodies / new, metadata)
        sync_directory(bodies)

    @staticmethod
    def copy_body(source: Path, destination: Path, metadata: os.stat_result) -> None:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.chown(destination, metadata.st_uid, metadata.st_gid)
        destination.chmod(0o600)
        with destination.open("rb") as copied, source.open("rb") as original:
            if (
                hashlib.file_digest(copied, "sha256").digest()
                != hashlib.file_digest(original, "sha256").digest()
            ):
                raise ServiceError("mailbox_import_body_verification_failed", 503)
            os.fsync(copied.fileno())

    def merge_live(self, target: Path, keys: dict[str, str]) -> int:
        """Merge the backed-up mail into Maddy's live WAL store in one write transaction."""
        from host.backup_merge import merge_rows

        data = self.live_data()
        with connect(data / "imapsql.db", LIVE_WRITE_WAIT_SECONDS) as live:
            live.execute("BEGIN IMMEDIATE")
            source = target / "maddy"
            return merge_rows(
                live, data / "messages", source / "imapsql.db", source / "messages", keys
            )

    def record_import(self, job: dict[str, Any], missing: set[str], target: Path) -> None:
        """Commit the marker with the dashboard and incarnation rows; safe to repeat."""
        paths = {
            "main": self.control.path,
            "dashboard": self.dashboard_database(),
            "state": self.context.coordination.journal.path,
        }
        with attached_transaction(paths) as connection:
            with sqlite3.connect(
                (target / "dashboard/admin_data.db").as_uri() + "?mode=ro", uri=True
            ) as source_metadata:
                source_metadata.row_factory = sqlite3.Row
                for email in sorted(missing):
                    self.restore_mailbox_rows(connection, source_metadata, email, job["id"])
            connection.execute(
                "INSERT OR IGNORE INTO backup_imports VALUES(?,?)", (job["id"], time.time())
            )

    @staticmethod
    def restore_mailbox_rows(
        connection: sqlite3.Connection, source: sqlite3.Connection, email: str, generation: str
    ) -> None:
        row = source.execute(
            "SELECT display_name,notes,created FROM mailboxes WHERE email=?", (email,)
        ).fetchone()
        if row is None:
            raise ServiceError("mailbox_metadata_missing", 409)
        connection.execute(
            "INSERT INTO dashboard.mailboxes(email,display_name,notes,created,updated,status) "
            "VALUES(?,?,?,?,?,'ready') ON CONFLICT(email) DO UPDATE SET "
            "display_name=excluded.display_name,notes=excluded.notes,"
            "created=excluded.created,updated=excluded.updated,status='ready'",
            (email, row["display_name"], row["notes"], row["created"], time.time()),
        )
        connection.execute(
            "INSERT INTO state.incarnations VALUES(?,?,?,1) ON "
            "CONFLICT(email) DO UPDATE SET "
            "generation=excluded.generation,created=excluded.created,present=1",
            (email, generation, time.time()),
        )

    def remove_unreferenced(self, keys: list[str]) -> None:
        """Only unreferenced files owned by this exact operation are disposable."""
        bodies = self.live_data() / "messages"
        with connect(self.live_data() / "imapsql.db") as live:
            for key in keys:
                if (bodies / key).is_symlink():
                    raise ServiceError("mailbox_import_body_conflict", 409)
                if not live.execute("SELECT 1 FROM extKeys WHERE id=?", (key,)).fetchone():
                    (bodies / key).unlink(missing_ok=True)
        sync_directory(bodies)

    def resume_capture_consumers(self) -> None:
        """A crashed safety backup can leave Maddy stopped for its capture; start it again."""
        maddy = DockerMaddy(self.context.deployment)
        writers = DockerSnapshots(
            (Docker(timeout=30), maddy, self.context.intents), startup_timeout=30
        )
        state = self.context.intents.read()
        for name, consumer in state["consumers"].items():
            if consumer.get("reason") == "backup_capture":
                if name == "maddy":
                    observed = self.context.docker.inspect(name)
                    if observed["Image"] != self.context.deployment["inputs"]["maddy_image_id"]:
                        raise ServiceError("maddy_identity_mismatch", 503)
                    mounts = {
                        entry["Destination"]: entry["Source"] for entry in observed["Mounts"]
                    }
                    if mounts != maddy.expected_mounts():
                        raise ServiceError("maddy_mount_mismatch", 503)
                writers._start_existing(name)
                writers._verify_resumed(name, consumer["served_sha256"])
                consumer.update(intent="expected_running", state="active")
                consumer.pop("reason", None)
        self.context.intents.write(state)

    def import_committed(self, original: dict[str, Any]) -> bool:
        """Committed means the marker exists, or the merge reached Maddy's store (its body keys)."""
        with self.control.connect() as ledger:
            if ledger.execute(
                "SELECT 1 FROM backup_imports WHERE id=?", (original["id"],)
            ).fetchone():
                return True
            plan = ledger.execute(
                "SELECT keys FROM backup_plans WHERE id=?", (original["id"],)
            ).fetchone()
        if original["kind"] != "restore":
            return False
        keys = json.loads(plan[0]) if plan else []
        if self.merged_keys(keys) if keys else self.merged_mailboxes(original):
            self.finish_import(original, keys)
            return True
        self.discard_staged(keys)
        return False

    def merged_mailboxes(self, original: dict[str, Any]) -> bool:
        """Without staged bodies (empty mailboxes only) the recreated accounts are the proof."""
        missing = self.control.job(original["payload"]["previous"])["result"]["missing"]
        with connect(self.live_data() / "imapsql.db") as live:
            return any(
                live.execute("SELECT 1 FROM users WHERE username=?", (email,)).fetchone()
                for email in missing
            )

    def merged_keys(self, keys: list[str]) -> bool:
        with connect(self.live_data() / "imapsql.db") as live:
            return any(
                live.execute("SELECT 1 FROM extKeys WHERE id=?", (key,)).fetchone()
                for key in keys
            )

    def finish_import(self, original: dict[str, Any], keys: list[str]) -> None:
        """The mail merge committed but its marker did not: complete the bookkeeping."""
        identifier = original["payload"]["previous"]
        missing = set(self.control.job(identifier)["result"]["missing"])
        self.record_import(original, missing, self.staging / identifier)
        self.remove_unreferenced(keys)

    def discard_staged(self, keys: list[str]) -> None:
        """Nothing merged, so the staged files are unreferenced leftovers of this operation."""
        bodies = self.live_data() / "messages"
        for key in keys:
            if (bodies / key).is_symlink():
                raise ServiceError("mailbox_import_body_conflict", 409)
            (bodies / key).unlink(missing_ok=True)
        sync_directory(bodies)

    def reconcile(self, job: dict[str, Any]) -> dict[str, Any]:
        original = self.control.job(job["payload"]["previous"], private=True)
        with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
            self.repository.process.reconcile()
            with self.context.coordination.consistent():
                self.resume_capture_consumers()
                committed = self.import_committed(original)
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


def interrupted(_signal: int, _frame: Any) -> None:
    raise ServiceError("backup_worker_interrupted", 503)
