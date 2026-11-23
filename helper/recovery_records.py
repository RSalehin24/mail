"""Recovery ledger, separate from the dashboard authentication database.

No record becomes restorable until an encrypted recovery point was verified.
Interrupted mutations remain explicit and cannot be retried as new operations.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from helper.retention import RecoveryPolicy
from helper.state_database import prepare_state
from shared.errors import ServiceError
from shared.validation import address, request_id


class RecoveryRecords:
    def __init__(self, path: Path) -> None:
        self.path = path
        prepare_state(path)

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
        with self.transaction() as connection:
            return bool(connection.execute(
                "SELECT 1 FROM recovery_records WHERE state IN "
                "('capturing','deleting','restoring','removing','reconciliation_required') LIMIT 1",
            ).fetchone())
