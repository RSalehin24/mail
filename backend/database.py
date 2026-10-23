"""Short SQLite transactions with explicit provisioning and bounded busy handling."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from backend.schema import INITIAL_SCHEMA
from shared.errors import ServiceError
from shared.validation import SCHEMA_VERSION


def require_existing_database(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ServiceError("installation_state_missing", 503)


class Database:
    def __init__(self, path: Path, identity: str) -> None:
        self.path = path
        self.identity = identity
        require_existing_database(path)
        with self.transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise ServiceError("incompatible_schema", 503)
            row = connection.execute("SELECT identity FROM installation WHERE id=1").fetchone()
            if row is None or row[0] != identity:
                raise ServiceError("installation_identity_mismatch", 503)
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ServiceError("storage_corrupt", 503)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            raise ServiceError("storage_unavailable", 503) from error
        finally:
            if connection is not None:
                connection.close()

    @classmethod
    def provision(cls, path: Path, identity: str) -> "Database":
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(path) as connection:
                connection.executescript(INITIAL_SCHEMA)
                connection.execute("INSERT INTO installation VALUES(1, ?)", (identity,))
                connection.execute("INSERT INTO admin_auth(id) VALUES(1)")
        except sqlite3.Error as error:
            raise ServiceError("provisioning_incomplete", 503) from error
        return cls(path, identity)

    def snapshot(self, destination: Path) -> None:
        descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with self.transaction() as source, sqlite3.connect(destination) as target:
                # A separate read connection avoids backing up the writer's own transaction.
                with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as reader:
                    reader.backup(target)
                source.execute("SELECT identity FROM installation")
        except sqlite3.Error as error:
            raise ServiceError("snapshot_failed", 503) from error
