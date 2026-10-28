"""Bounded root worker for isolated mailbox deletion, restoration and retention."""

import json
import signal
import sqlite3
import time
from pathlib import Path
from typing import Any

from helper.maddy import DockerMaddy
from helper.recovery import RecoveryControl
from helper.recovery_records import RecoveryRecords
from helper.retention import RecoveryPolicy
from host.context import HostContext
from host.docker import Docker, DockerSnapshots
from host.locks import HostLock, LockPolicy
from host.mailbox_archive import MailboxArchives
from host.mailbox_store import body_mapping, body_path, connect, import_rows, stage_bodies, validate
from host.mailbox_transaction import mailbox_transaction
from shared.errors import ServiceError
from shared.files import protected_json, sync_directory


class MailboxRecovery:
    def __init__(self, context: HostContext, root: Path) -> None:
        self.context, self.root = context, root
        self.records = RecoveryRecords(root / "recovery.db")
        self.control = RecoveryControl(root, self.records)
        if not (root / 'recovery.key').exists() and self.records.pinned_snapshots():
            raise ServiceError('recovery_key_missing', 503)
        self.archives = MailboxArchives(root / "recovery-points", root / "recovery.key")
        self.data = Path(context.deployment["maddy_data_directory"])
        paths = context.deployment["maddy_message_paths"]
        if len(paths) != 1 or paths[0] != "messages":
            raise ServiceError("mailbox_message_store_unsupported", 503)
        self.bodies = self.data / "messages"
        self.maddy = DockerMaddy(context.deployment)
        self.writers = DockerSnapshots((Docker(timeout=30), self.maddy, context.generations),
                                       startup_timeout=30)

    def run(self) -> None:
        lock = self.root / "recovery-worker.lock"
        with HostLock(lock, LockPolicy(0.1)):
            job = self.records.claim(int(time.time()))
            if job is not None:
                try:
                    self._perform(job)
                    if job['kind'] == 'recovery_reconcile':
                        with self.records.transaction() as connection:
                            connection.execute(
                                "UPDATE recovery_jobs SET state='complete',error='',updated_at=? "
                                "WHERE record_id=? AND state='reconciliation_required'",
                                (int(time.time()), job['record_id']),
                            )
                    self.records.finish(job["id"], "complete", int(time.time()))
                except (OSError, ValueError, sqlite3.Error, ServiceError) as error:
                    code = error.code if isinstance(error, ServiceError) else "recovery_io_failure"
                    state = "reconciliation_required"
                    try:
                        record = self.records.get(job["record_id"])
                        if job["kind"] == "recovery_restore" and record["state"] == "deleted":
                            state = "failed"  # Refused before import began; safe to try again.
                    except ServiceError as missing:
                        if missing.code == "deleted_mailbox_not_found":
                            state = "failed"  # Policy/inventory refused before a deletion began.
                    self.records.finish(job["id"], state, int(time.time()), code)
                    # Never automatically repeat a mailbox mutation after an unknown outcome.
                    return
            self._expire()

    def _perform(self, job: dict[str, Any]) -> None:
        kind = job["kind"]
        if kind == "recovery_delete":
            self._delete(job)
        elif kind == "recovery_restore":
            with self.context.coordination.consistent():
                self._restore(job)
        elif kind == "recovery_remove":
            if not self.control.policy()["permanent_deletion_enabled"]:
                raise ServiceError("permanent_deletion_disabled", 403)
            self._remove(job["record_id"])
        elif kind == "recovery_reconcile":
            self._reconcile(job)
        else:
            raise ServiceError("invalid_recovery_operation", 503)

    def _check_delete_policy(self, job: dict[str, Any]) -> RecoveryPolicy:
        policy = self.control.policy()
        payload = job["payload"]
        if policy["revision"] != payload["policy_revision"]:
            raise ServiceError("recovery_policy_changed", 409)
        parsed = RecoveryPolicy.parse(policy)
        parsed.deadline(payload["retention"], int(time.time()))
        if job["email"] in policy["protected"]:
            raise ServiceError("protected_recipient", 403)
        if payload["permanent"] and not policy["permanent_deletion_enabled"]:
            raise ServiceError("permanent_deletion_disabled", 403)
        return parsed

    def _metadata(self, email: str) -> dict[str, Any]:
        database = Path(self.context.deployment["backup_sources"]["dashboard"]) / "admin_data.db"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=0.25) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT display_name,notes,created FROM mailboxes WHERE email=?", (email,)
            ).fetchone()
            if row is None:
                raise ServiceError("mailbox_metadata_missing", 409)
            return dict(row)

    def _delete(self, job: dict[str, Any]) -> None:
        with self.context.coordination.consistent():
            policy = self._check_delete_policy(job)
            self.maddy.validate()
            inventory = self.maddy.inventories()
            if any(job["email"] not in inventory[key] for key in ("credentials", "storage")):
                raise ServiceError("account_conflict", 409)
            self.archives.admit(self.data / 'imapsql.db', self.bodies, job['email'])
            metadata = self._metadata(job["email"])
            self.context.coordination.journal.synchronize(inventory, time.time())
            generation = self.context.coordination.journal.incarnation(job['email'])['generation']
            job['payload']['generation'] = generation
            job['captured_metadata'] = metadata
            record = self.records.begin(
                job["record_id"],
                job["email"],
                policy,
                job["payload"]["retention"],
                int(time.time()),
                metadata["display_name"],
                metadata["notes"],
            )
            if record["state"] != "capturing":
                raise ServiceError("recovery_state_conflict", 409)
            try:
                self.writers.pause(["maddy"])
                if not job["payload"]["permanent"]:
                    snapshot = self.archives.create(
                        job["record_id"],
                        self.data / "imapsql.db",
                        self.bodies,
                        job["email"],
                        metadata,
                    )
                    self.records.transition(
                        job["record_id"],
                        "capturing",
                        "deleting",
                        int(time.time()),
                        verified_snapshot=snapshot,
                    )
                else:
                    # Permanent removal deliberately creates no new recovery copy.
                    with self.records.transaction() as connection:
                        connection.execute(
                            "UPDATE recovery_records SET state='deleting' WHERE id=?",
                            (job["record_id"],),
                        )
                self._remove_live(job)
            finally:
                self.writers.resume(["maddy"])
            self._cleanup_deleted(job)

    def _paths(self) -> dict[str, Path]:
        return {'main': self.data / 'imapsql.db',
                'credentials': self.data / 'credentials.db',
                'dashboard': Path(self.context.deployment['backup_sources']['dashboard'])
                / 'admin_data.db',
                'incarnations': self.context.coordination.journal.path,
                'ledger': self.records.path}

    def _phase(self, job: dict[str, Any], phase: str, details: dict[str, Any]) -> None:
        self.records.checkpoint(job['id'], job['record_id'], phase, details, int(time.time()))

    def _committed_phase(self, connection: sqlite3.Connection, job: dict[str, Any],
                         phase: str, details: dict[str, Any]) -> None:
        connection.execute(
            'INSERT OR REPLACE INTO ledger.recovery_checkpoints VALUES(?,?,?,?,?)',
            (job['id'], job['record_id'], phase, json.dumps(details), int(time.time())),
        )

    def _remove_live(self, job: dict[str, Any]) -> None:
        email = job['email']
        with connect(self.data / 'imapsql.db') as reader:
            account = reader.execute('SELECT id FROM users WHERE username=?', (email,)).fetchone()
            if account is None:
                raise ServiceError('account_conflict', 409)
            keys = [row[0] for row in reader.execute('SELECT id FROM extKeys WHERE uid=?',
                                                    (account[0],))]
            for key in keys:
                body_path(self.bodies, key)
        details = {'keys': keys, 'generation': job['payload']['generation']}
        self._phase(job, 'deletion_transaction', details)
        with mailbox_transaction(self._paths()) as connection:
            validate(connection)
            generation = connection.execute(
                'SELECT generation,present FROM incarnations.incarnations WHERE email=?',
                (email,),
            ).fetchone()
            if generation is None or tuple(generation) != (details['generation'], 1):
                raise ServiceError('mailbox_generation_conflict', 409)
            connection.execute('DELETE FROM credentials.passwords WHERE key=?', (email,))
            connection.execute('DELETE FROM users WHERE username=?', (email,))
            connection.execute('DELETE FROM extKeys WHERE uid=?', (account[0],))
            row = connection.execute(
                'SELECT display_name,notes,created FROM dashboard.mailboxes WHERE email=?',
                (email,),
            ).fetchone()
            if row is None or dict(row) != job['captured_metadata']:
                raise ServiceError('mailbox_metadata_changed', 409)
            connection.execute('DELETE FROM dashboard.mailboxes WHERE email=?', (email,))
            connection.execute('UPDATE incarnations.incarnations SET present=0 WHERE email=?',
                               (email,))
            now = int(time.time())
            policy = RecoveryPolicy.parse(self.control.policy())
            final = 'removed' if job['payload']['permanent'] else 'deleted'
            connection.execute(
                'UPDATE ledger.recovery_records SET state=?,updated_at=?,deleted_at=?,deadline=? '
                'WHERE id=?', (final, now, now, policy.deadline(job['payload']['retention'], now),
                              job['record_id']),
            )
            self._committed_phase(connection, job, 'deleted_body_cleanup', details)

    def _cleanup_deleted(self, job: dict[str, Any]) -> None:
        checkpoint = self.records.checkpoint_for(job['record_id'])
        if checkpoint is None or checkpoint['phase'] != 'deleted_body_cleanup':
            return
        with connect(self.data / 'imapsql.db') as connection:
            for key in checkpoint['details']['keys']:
                if connection.execute('SELECT 1 FROM extKeys WHERE id=?', (key,)).fetchone():
                    raise ServiceError('mailbox_generation_conflict', 409)
                path = self.bodies / key
                if path.is_symlink():
                    raise ServiceError('mailbox_body_key_invalid', 503)
                path.unlink(missing_ok=True)
        sync_directory(self.bodies)
        self.records.checkpoint(checkpoint['job_id'], job['record_id'], 'complete',
                                checkpoint['details'], int(time.time()))

    def _restore(self, job: dict[str, Any], *, resuming: bool = False) -> None:
        record = self.records.get(job['record_id'])
        allowed = {'restoring'} if resuming else {'deleted', 'cancelled'}
        if record['state'] not in allowed or (not resuming and record['deadline'] is not None
                                             and int(time.time()) >= record['deadline']):
            raise ServiceError('recovery_expired', 409)
        # Decrypt/verify outside the mail interruption, inside the operation lock.
        with self.archives.open(record['snapshot'], job['email']) as isolated:
            self.archives.admit(isolated / 'imapsql.db', isolated / 'bodies', job['email'])
            self.maddy.validate()
            inventory = self.maddy.inventories()
            if any(job['email'] in inventory[key] for key in ('credentials', 'storage')):
                raise ServiceError('account_conflict', 409)
            keys = body_mapping(isolated, job['id'])
            checkpoint = self.records.checkpoint_for(record['id']) if resuming else None
            if checkpoint and checkpoint['details'].get('keys') != keys:
                raise ServiceError('recovery_checkpoint_conflict', 409)
            if not resuming:
                with connect(self.data / 'imapsql.db') as live:
                    if any((self.bodies / key).exists() or live.execute(
                        'SELECT 1 FROM extKeys WHERE id=?', (key,),
                    ).fetchone() for key in keys.values()):
                        raise ServiceError('mailbox_import_body_conflict', 409)
                self._phase(job, 'restore_body_staging', {'keys': keys})
                self.records.transition(
                    record['id'], record['state'], 'restoring', int(time.time()),
                )
            try:
                self.writers.pause(['maddy'])
                with connect(self.data / 'imapsql.db') as live:
                    stage_bodies(live, self.bodies, isolated, keys, resume=resuming)
                self._phase(job, 'restore_transaction', {'keys': keys})
                with mailbox_transaction(self._paths()) as connection:
                    userid = import_rows(connection, job['email'], isolated, keys)
                    connection.execute('INSERT INTO credentials.passwords(key,value) VALUES(?,?)',
                                       (job['email'], job['payload']['password_hash']))
                    self._restore_metadata(connection, job['email'],
                                           protected_json(isolated / 'metadata.json'))
                    connection.execute(
                        'INSERT INTO incarnations.incarnations VALUES(?,?,?,1) '
                        'ON CONFLICT(email) DO UPDATE SET generation=excluded.generation,'
                        'created=excluded.created,present=1',
                        (job['email'], job['id'], time.time()),
                    )
                    connection.execute(
                        "UPDATE ledger.recovery_records SET state='restored',updated_at=? "
                        "WHERE id=?",
                        (int(time.time()), record['id']),
                    )
                    self._committed_phase(connection, job, 'restore_committed',
                                          {'keys': keys, 'user_id': userid,
                                           'generation': job['id']})
            finally:
                self.writers.resume(['maddy'])
            self._phase(job, 'complete', {'keys': keys, 'generation': job['id']})

    def _restore_metadata(self, connection: sqlite3.Connection, email: str,
                          metadata: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO dashboard.mailboxes(email,display_name,notes,created,updated,status) "
            "VALUES(?,?,?,?,?,'ready') ON CONFLICT(email) DO UPDATE SET "
            "display_name=excluded.display_name,notes=excluded.notes,created=excluded.created,"
            "updated=excluded.updated,status='ready'",
            (email, metadata['display_name'], metadata['notes'], metadata['created'], time.time()),
        )

    def _remove(self, identifier: str) -> None:
        with self.context.coordination.consistent():
            record = self.records.get(identifier)
            with self.records.transaction() as connection:
                connection.execute(
                    'INSERT INTO recovery_audit(created,action,request_id,email,outcome) '
                    'VALUES(?,?,?,?,?)',
                    (int(time.time()), 'recovery_point_removal', identifier,
                     record['email'], 'intent_recorded'),
                )
            self.records.transition(identifier, record["state"], "removing", int(time.time()))
            self.archives.remove(record["snapshot"])
            self.records.transition(identifier, "removing", "removed", int(time.time()))
            with self.records.transaction() as connection:
                connection.execute(
                    'INSERT INTO recovery_audit(created,action,request_id,email,outcome) '
                    'VALUES(?,?,?,?,?)',
                    (int(time.time()), 'recovery_point_removal', identifier,
                     record['email'], 'complete'),
                )

    def _reconcile(self, job: dict[str, Any]) -> None:
        try:
            record = self.records.get(job['record_id'])
        except ServiceError as error:
            if error.code != 'deleted_mailbox_not_found':
                raise
            original = self.records.original_job(job['record_id'])
            if original['kind'] != 'recovery_delete':
                raise ServiceError('recovery_checkpoint_missing', 409) from error
            # No recovery record means the worker never passed admission/begin;
            # no live mailbox mutation can have started. Cancel only this job.
            with self.context.coordination.consistent():
                with self.records.transaction() as connection:
                    connection.execute(
                        "UPDATE recovery_jobs SET state='failed',error=?,updated_at=? WHERE id=?",
                        ('interrupted_before_mailbox_mutation', int(time.time()), original['id']),
                    )
            return
        if record["state"] == "removing":
            with self.context.coordination.consistent():
                self.archives.remove(record["snapshot"])
                self.records.transition(record["id"], "removing", "removed", int(time.time()))
            return
        with self.context.coordination.consistent():
            state = self.context.generations.read()
            consumer = state["consumers"]["maddy"]
            observed = self.context.docker.inspect("maddy")
            if consumer.get("reason") == "backup_capture":
                if observed["Image"] != self.context.deployment["inputs"]["maddy_image_id"]:
                    raise ServiceError("maddy_identity_mismatch", 503)
                mounts = {entry["Destination"]: entry["Source"] for entry in observed["Mounts"]}
                if mounts != self.maddy.expected_mounts():
                    raise ServiceError("maddy_mount_mismatch", 503)
                self.writers._start_existing("maddy")
                self.writers._verify_resumed("maddy", consumer["served_sha256"])
                consumer.update(intent="expected_running", state="active")
                consumer.pop("reason", None)
                self.context.generations.write(state)
            self.maddy.validate()
            inventory = self.maddy.inventories()
            credential = job["email"] in inventory["credentials"]
            storage = job["email"] in inventory["storage"]
            checkpoint = self.records.checkpoint_for(record['id'])
            if record['state'] == 'restoring':
                if checkpoint is None:
                    raise ServiceError('recovery_checkpoint_missing', 409)
                original = self.records.original_job(checkpoint['job_id'])
                self._restore(original, resuming=True)
                return
            if credential != storage:
                raise ServiceError("partial_account_requires_operator_repair", 409)
            if record["state"] in {"deleted", "restored", "removed"}:
                if (
                    (record["state"] == "deleted" and not storage)
                    or (record["state"] == "restored" and storage)
                    or record["state"] == "removed"
                ):
                    if record['state'] in {'deleted', 'removed'}:
                        self._cleanup_deleted(job)
                    if record['state'] == 'restored':
                        if checkpoint is None or self.context.coordination.journal.incarnation(
                            job['email'],
                        )['generation'] != checkpoint['details']['generation']:
                            raise ServiceError('mailbox_generation_conflict', 409)
                        self.records.checkpoint(checkpoint['job_id'], record['id'], 'complete',
                                                checkpoint['details'], int(time.time()))
                    return
                raise ServiceError("account_conflict", 409)
            if record["state"] in {"capturing", "deleting"} and not storage:
                if not record["snapshot"]:
                    # Permanent deletion has no recovery point to verify.
                    final = "removed"
                else:
                    with self.archives.open(record["snapshot"], job["email"]):
                        pass
                    final = "deleted"
                with sqlite3.connect(self._paths()['dashboard']) as database:
                    database.execute('DELETE FROM mailboxes WHERE email=?', (job['email'],))
            elif record["state"] in {"capturing", "deleting"} and storage:
                # Cancellation preserves the current mailbox and its separate recovery copy.
                final = "cancelled" if record["snapshot"] else "removed"
            else:
                # An interrupted import may have committed only some databases.
                # Refuse to infer success or merge an existing mailbox.
                raise ServiceError("restore_requires_operator_reconciliation", 409)
            with self.records.transaction() as connection:
                connection.execute(
                    "UPDATE recovery_records SET state=?,updated_at=? WHERE id=?",
                    (final, int(time.time()), record["id"]),
                )

    def _expire(self) -> None:
        for record in self.records.expired(int(time.time())):
            self._remove(record["id"])


def interrupted(_signal: int, _frame: Any) -> None:
    raise ServiceError("recovery_worker_interrupted", 503)


def main() -> None:
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(480)
    MailboxRecovery(HostContext.load(), Path("/var/lib/mail-admin-helper")).run()


if __name__ == "__main__":
    main()
