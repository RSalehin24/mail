"""Root-owned recovery ledger, separate from the dashboard authentication database.

No record becomes restorable until an encrypted recovery point was verified.
Interrupted mutations remain explicit and cannot be retried as new operations.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from helper.retention import RecoveryPolicy
from shared.errors import ServiceError
from shared.validation import address, request_id

# Jobs waiting or running at once; keeps a runaway client from filling the ledger.

class RecoveryRecords:
    def __init__(self, path: Path) -> None:
        self.path = path
        if path.is_symlink():
            raise ServiceError("recovery_storage_invalid", 503)
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        if not path.is_file() or path.stat().st_mode & 0o077:
            raise ServiceError("recovery_storage_invalid", 503)
        with self.transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ServiceError("recovery_schema_unsupported", 503)
            if version == 3:
                # Existing workers/helpers must not rewrite the ledger merely
                # by starting while a whole-host backup copies the quiet state.
                for table in ('recovery_records', 'recovery_jobs', 'recovery_audit',
                              'recovery_checkpoints'):
                    connection.execute(f'SELECT 1 FROM {table} LIMIT 1')  # noqa: S608 -- fixed tables
                return
            connection.execute("""CREATE TABLE IF NOT EXISTS recovery_records (
                id TEXT PRIMARY KEY, email TEXT NOT NULL, state TEXT NOT NULL,
                retention TEXT NOT NULL, deleted_at INTEGER NOT NULL,
                deadline INTEGER, policy_revision INTEGER NOT NULL,
                snapshot TEXT, display_name TEXT NOT NULL, notes TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS recovery_jobs (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, record_id TEXT NOT NULL,
                email TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                updated_at INTEGER NOT NULL, error TEXT NOT NULL DEFAULT ''
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS recovery_audit (
                id INTEGER PRIMARY KEY, created INTEGER NOT NULL, action TEXT NOT NULL,
                request_id TEXT NOT NULL, email TEXT NOT NULL, outcome TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS recovery_checkpoints (
                job_id TEXT PRIMARY KEY, record_id TEXT NOT NULL, phase TEXT NOT NULL,
                details TEXT NOT NULL, updated_at INTEGER NOT NULL
            )""")
            connection.execute("PRAGMA user_version=3")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise ServiceError("recovery_storage_unavailable", 503) from error
        finally:
            connection.close()

    def begin(
        self,
        identifier: str,
        email: str,
        policy: RecoveryPolicy,
        retention: str,
        now: int,
        display_name: str,
        notes: str,
    ) -> dict[str, Any]:
        identifier, email = request_id(identifier), address(email)
        deadline = policy.deadline(retention, now)
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM recovery_records WHERE id=?",
                (identifier,),
            ).fetchone()
            if previous:
                if previous["email"] != email or previous["retention"] != retention:
                    raise ServiceError("request_id_conflict", 409)
                return dict(previous)
            if connection.execute(
                "SELECT 1 FROM recovery_records WHERE state IN "
                "('capturing','deleting','restoring','removing','reconciliation_required')",
            ).fetchone():
                raise ServiceError("reconciliation_required", 409)
            connection.execute(
                "INSERT INTO recovery_records VALUES(?,?, 'capturing', ?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    email,
                    retention,
                    now,
                    deadline,
                    policy.revision,
                    None,
                    display_name,
                    notes,
                    now,
                ),
            )
            return dict(
                connection.execute(
                    "SELECT * FROM recovery_records WHERE id=?",
                    (identifier,),
                ).fetchone()
            )

    def transition(
        self,
        identifier: str,
        expected: str,
        target: str,
        now: int,
        *,
        verified_snapshot: str | None = None,
    ) -> None:
        transitions = {
            ("capturing", "deleting"),
            ("deleting", "deleted"),
            ("deleted", "restoring"),
            ("restoring", "restored"),
            ("deleted", "removing"),
            ("restored", "removing"),
            ("cancelled", "removing"),
            ("cancelled", "restoring"),
            ("removing", "removed"),
        }
        if (expected, target) not in transitions and not (
            expected in {"capturing", "deleting", "restoring", "removing"}
            and target == "reconciliation_required"
        ):
            raise ServiceError("invalid_recovery_transition", 409)
        if target == "deleting" and not verified_snapshot:
            raise ServiceError("verified_recovery_point_required", 409)
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE recovery_records SET state=?,updated_at=?,snapshot=COALESCE(?,snapshot) "
                "WHERE id=? AND state=?",
                (target, now, verified_snapshot, identifier, expected),
            )
            if result.rowcount != 1:
                raise ServiceError("recovery_state_conflict", 409)

    def expired(self, now: int) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM recovery_records "
                    "WHERE state IN ('deleted','restored','cancelled') "
                    "AND snapshot IS NOT NULL AND deadline IS NOT NULL "
                    "AND deadline<=? ORDER BY deadline,id",
                    (now,),
                )
            ]

    def pinned_snapshots(self) -> set[str]:
        # Expiry alone does not unpin a snapshot. Explicit successful cleanup of
        # every retained mailbox is required before ordinary backup pruning.
        with self.transaction() as connection:
            return {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT snapshot FROM recovery_records WHERE snapshot IS NOT NULL "
                    "AND state NOT IN ('removed')",
                )
            }

    def get(self, identifier: str) -> dict[str, Any]:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM recovery_records WHERE id=?",
                (request_id(identifier),),
            ).fetchone()
            if row is None:
                raise ServiceError("deleted_mailbox_not_found", 404)
            return dict(row)

    def listing(self) -> list[dict[str, Any]]:
        with self.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM recovery_records WHERE state NOT IN ('removed') "
                    "ORDER BY deleted_at DESC,id",
                )
            ]

    def busy(self) -> bool:
        """Whether a deletion or restore is part-way, so other changes should wait."""
        with self.transaction() as connection:
            return bool(connection.execute(
                "SELECT 1 FROM recovery_records WHERE state IN "
                "('capturing','deleting','restoring','removing','reconciliation_required') LIMIT 1",
            ).fetchone())
