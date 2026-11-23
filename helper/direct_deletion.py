"""Delete one mailbox while Maddy keeps running: archive it, remove it, record the result.

The mail database is in WAL mode, so a snapshot never blocks Maddy. The account's rows are removed
inside one short write lock, and only if nothing in the account changed since the snapshot, so no
message that arrives during the archive is lost. Mail written by other mailboxes is not affected.
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helper.journal import Journal
from helper.maddy import MaddyAccess
from helper.recovery_records import RecoveryRecords
from helper.retention import RecoveryPolicy
from host.mailbox_archive import MailboxArchives
from host.mailbox_store import body_path
from host.mailbox_transaction import attached_transaction
from shared.errors import ServiceError

CAPTURE_ATTEMPTS = 5
IN_PROGRESS = {"capturing", "deleting"}
WRITE_LOCK_WAIT_SECONDS = 5.0
ACCOUNT_FINGERPRINT = (
    "WITH folders AS (SELECT id FROM mboxes WHERE uid=?1) SELECT "
    "(SELECT COUNT(*) FROM msgs WHERE mboxId IN folders),"
    "(SELECT COALESCE(SUM(msgId),0) FROM msgs WHERE mboxId IN folders),"
    "(SELECT COALESCE(SUM(seen),0) FROM msgs WHERE mboxId IN folders),"
    "(SELECT COUNT(*) FROM flags WHERE mboxId IN folders),"
    "(SELECT COUNT(*)||':'||COALESCE(SUM(uidnext),0) FROM mboxes WHERE uid=?1)"
)


@dataclass(frozen=True)
class DeletionStorage:
    data: Path
    dashboard_database: Path


@dataclass(frozen=True)
class DeletionRequest:
    identifier: str
    email: str
    retention: str
    permanent: bool
    metadata: dict[str, Any]


class DirectDeletion:
    def __init__(
        self,
        storage: DeletionStorage,
        services: tuple[MaddyAccess, Journal, RecoveryRecords, Callable[[], MailboxArchives]],
    ) -> None:
        self.storage = storage
        self.maddy, self.journal, self.records, self.archives_factory = services

    def delete(self, request: DeletionRequest, policy: RecoveryPolicy, now: int) -> dict[str, Any]:
        previous = self._previous(request.identifier)
        if previous is not None and previous["state"] not in IN_PROGRESS:
            return self._complete(request)  # A repeated request gets the original answer.
        stored = self._stored(request.email)
        if not stored and (previous is None or previous["state"] != "deleting"):
            raise ServiceError("account_not_found", 404)
        self.records.begin(
            request.identifier, request.email, policy, request.retention, now,
            request.metadata["display_name"], request.metadata["notes"],
        )
        if stored:
            self._remove_storage(request)
        self._record_outcome(request, policy)
        return self._complete(request)

    @staticmethod
    def _complete(request: DeletionRequest) -> dict[str, Any]:
        return {"request_id": request.identifier, "state": "complete", "error": ""}

    def _previous(self, identifier: str) -> dict[str, Any] | None:
        try:
            return self.records.get(identifier)
        except ServiceError as error:
            if error.code != "deleted_mailbox_not_found":
                raise
            return None

    def _stored(self, email: str) -> bool:
        """Whether the mailbox is in Maddy; half-present accounts are a conflict, not a guess."""
        self.maddy.validate()
        inventory = self.maddy.inventories()
        present = [email in inventory[key] for key in ("credentials", "storage")]
        if any(present) and not all(present):
            raise ServiceError("account_conflict", 409)
        if all(present):
            self.journal.synchronize(inventory, time.time())
        return all(present)

    def _remove_storage(self, request: DeletionRequest) -> None:
        for _ in range(CAPTURE_ATTEMPTS):
            keys = self._archive_and_remove(request)
            if keys is not None:
                self._delete_bodies(keys)
                return
        raise ServiceError("mailbox_changing_retry", 409)

    def _archive_and_remove(self, request: DeletionRequest) -> list[str] | None:
        data = self.storage.data
        before = self._fingerprint(data / "imapsql.db", request.email)
        snapshot = None
        if not request.permanent:
            archives = self.archives_factory()
            archives.remove(request.identifier)
            snapshot = archives.create(
                request.identifier, data / "imapsql.db", data / "messages", request.email,
                request.metadata,
            )
        return self._remove_rows(request, before, snapshot)

    def _fingerprint(self, database: Path, email: str) -> tuple[Any, ...]:
        with self._connect(database) as connection:
            return self._account_state(connection, email)[1]

    @staticmethod
    def _connect(database: Path) -> sqlite3.Connection:
        if database.is_symlink() or not database.is_file():
            raise ServiceError("mailbox_store_missing", 503)
        connection = sqlite3.connect(
            database.as_uri() + "?mode=rw", uri=True, timeout=WRITE_LOCK_WAIT_SECONDS,
            isolation_level=None,
        )
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _account_state(connection: sqlite3.Connection, email: str) -> tuple[int, tuple[Any, ...]]:
        row = connection.execute("SELECT id FROM users WHERE username=?", (email,)).fetchone()
        if row is None:
            raise ServiceError("account_conflict", 409)
        return row[0], tuple(connection.execute(ACCOUNT_FINGERPRINT, (row[0],)).fetchone())

    def _remove_rows(
        self, request: DeletionRequest, expected: tuple[Any, ...], snapshot: str | None
    ) -> list[str] | None:
        connection = self._connect(self.storage.data / "imapsql.db")
        try:
            connection.execute("BEGIN IMMEDIATE")
            account, current = self._account_state(connection, request.email)
            if current != expected:
                connection.execute("ROLLBACK")
                return None  # Mail arrived or changed while archiving; archive again.
            keys = [row[0] for row in connection.execute(
                "SELECT id FROM extKeys WHERE uid=?", (account,))]
            for key in keys:
                body_path(self.storage.data / "messages", key)
            self._mark_deleting(request, snapshot)
            connection.execute("DELETE FROM users WHERE username=?", (request.email,))
            connection.execute("DELETE FROM extKeys WHERE uid=?", (account,))
            connection.execute("COMMIT")
            return keys
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def _mark_deleting(self, request: DeletionRequest, snapshot: str | None) -> None:
        if snapshot is not None:
            self.records.transition(
                request.identifier, "capturing", "deleting", int(time.time()),
                verified_snapshot=snapshot,
            )
            return
        with self.records.transaction() as connection:
            connection.execute(
                "UPDATE recovery_records SET state='deleting' WHERE id=?", (request.identifier,)
            )

    def _delete_bodies(self, keys: list[str]) -> None:
        bodies = self.storage.data / "messages"
        for key in keys:
            path = bodies / key
            if path.is_symlink():
                raise ServiceError("mailbox_body_key_invalid", 503)
            path.unlink(missing_ok=True)

    def _record_outcome(self, request: DeletionRequest, policy: RecoveryPolicy) -> None:
        now = int(time.time())
        final = "removed" if request.permanent else "deleted"
        paths = {
            "main": self.storage.data / "credentials.db",
            "dashboard": self.storage.dashboard_database,
            "incarnations": self.journal.path,
            "ledger": self.records.path,
        }
        with attached_transaction(paths) as connection:
            connection.execute("DELETE FROM passwords WHERE key=?", (request.email,))
            connection.execute("DELETE FROM dashboard.mailboxes WHERE email=?", (request.email,))
            connection.execute(
                "UPDATE incarnations.incarnations SET present=0 WHERE email=?", (request.email,))
            connection.execute(
                "UPDATE ledger.recovery_records SET state=?,updated_at=?,deleted_at=?,deadline=? "
                "WHERE id=?",
                (final, now, now, policy.deadline(request.retention, now), request.identifier),
            )
            connection.execute(
                "INSERT INTO ledger.recovery_audit(created,action,request_id,email,outcome) "
                "VALUES(?,?,?,?,?)",
                (now, "recovery_delete", request.identifier, request.email, "complete"),
            )
