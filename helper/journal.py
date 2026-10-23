"""Root-owned durable records. In-flight steps are never replayed after a crash."""

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from helper.protocol import HelperRequest
from shared.errors import ServiceError


class Journal:
    def __init__(self, path: Path, owner: int = 0) -> None:
        self.path = path
        if not path.is_file() or path.is_symlink():
            raise ServiceError("journal_missing", 503)
        metadata = path.lstat()
        if metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ServiceError("unsafe_journal_file", 503)
        with self.connection() as connection:
            connection.execute("SELECT id FROM operations LIMIT 1")

    @classmethod
    def provision(cls, path: Path) -> "Journal":
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE operations(id TEXT PRIMARY KEY, operation TEXT NOT NULL, "
                "email TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, "
                "state TEXT NOT NULL, phase TEXT, steps TEXT NOT NULL, "
                "preconditions TEXT NOT NULL, postconditions TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE incarnations(email TEXT PRIMARY KEY,generation TEXT NOT NULL, "
                "created REAL NOT NULL,present INTEGER NOT NULL)"
            )
        return cls(path, os.geteuid())

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            raise ServiceError("journal_unavailable", 503) from error
        finally:
            if connection is not None:
                connection.close()

    def lookup(self, identifier: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            for field in ("steps", "preconditions", "postconditions"):
                record[field] = json.loads(record[field])
            return record

    def begin(self, request: HelperRequest, values: tuple[float, dict[str, Any]]) -> dict[str, Any]:
        now, preconditions = values
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,'pending',NULL,'[]',?,'{}')",
                (
                    request.request_id,
                    request.operation,
                    request.email,
                    now,
                    now,
                    json.dumps(preconditions),
                ),
            )
        result = self.lookup(request.request_id)
        if result is None:
            raise ServiceError("journal_unavailable", 503)
        return result

    def update(self, identifier: str, values: dict[str, Any]) -> dict[str, Any]:
        record = self.lookup(identifier)
        if record is None or not set(values) <= {
            "state",
            "phase",
            "steps",
            "postconditions",
            "updated",
        }:
            raise ServiceError("journal_record_invalid", 503)
        record.update(values)
        with self.connection() as connection:
            connection.execute(
                "UPDATE operations SET state=?,phase=?,steps=?,postconditions=?,updated=? "
                "WHERE id=?",
                (
                    record["state"],
                    record["phase"],
                    json.dumps(record["steps"]),
                    json.dumps(record["postconditions"]),
                    record["updated"],
                    identifier,
                ),
            )
        return record

    def unresolved(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,email,state,phase FROM operations "
                    "WHERE state IN ('pending','unknown','reconciliation_required')"
                )
            ]

    def retain(self, now: float) -> None:
        with self.connection() as connection:
            connection.execute(
                "DELETE FROM operations WHERE id IN (SELECT id FROM operations "
                "WHERE state IN ('complete','failed','conflict') AND updated<? LIMIT 500)",
                (now - 90 * 86400,),
            )

    def synchronize(self, inventory: dict[str, list[str]], now: float) -> None:
        import uuid

        current = set(inventory["credentials"]) | set(inventory["storage"])
        with self.connection() as connection:
            previous = {
                row["email"]: row for row in connection.execute("SELECT * FROM incarnations")
            }
            for email in current:
                if email not in previous or not previous[email]["present"]:
                    connection.execute(
                        "INSERT OR REPLACE INTO incarnations VALUES(?,?,?,1)",
                        (email, str(uuid.uuid4()), now),
                    )
            for email in set(previous) - current:
                connection.execute("UPDATE incarnations SET present=0 WHERE email=?", (email,))

    def incarnation(self, email: str) -> dict[str, Any]:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM incarnations WHERE email=? AND present=1", (email,)
            ).fetchone()
            if row is None:
                raise ServiceError("account_incarnation_unknown", 409)
            return dict(row)
