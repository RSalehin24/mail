"""Isolated extraction and row-level import for the pinned imapsql schema.

Callers must stop the mail writer while accessing the live store. Import never
replaces the shared database, preserves mailbox content and refuses an address
conflict. Recovery databases contain only the selected account.
"""

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import sync_directory
from shared.validation import address

COLUMNS = {
    "users": {"id", "username", "msgsizelimit", "inboxId"},
    "mboxes": {
        "id",
        "uid",
        "name",
        "sub",
        "mark",
        "msgsizelimit",
        "uidnext",
        "uidvalidity",
        "specialuse",
        "msgsCount",
    },
    "extKeys": {"id", "uid", "refs"},
    "msgs": {
        "mboxId",
        "msgId",
        "date",
        "bodyLen",
        "mark",
        "bodyStructure",
        "cachedHeader",
        "extBodyKey",
        "seen",
        "compressAlgo",
        "recent",
    },
    "flags": {"mboxId", "msgId", "flag"},
}


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    if path.is_symlink() or not path.is_file():
        raise ServiceError("mailbox_store_missing", 503)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA synchronous=FULL")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def validate(connection: sqlite3.Connection) -> None:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    if tables != set(COLUMNS) | {'schema_version'} or connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1"
    ).fetchone():
        raise ServiceError('mailbox_store_schema_unsupported', 503)
    if connection.execute("SELECT version FROM schema_version").fetchall()[0][0] != 6:
        raise ServiceError("mailbox_store_schema_unsupported", 503)
    for table, columns in COLUMNS.items():
        if {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')} != columns:
            raise ServiceError("mailbox_store_schema_unsupported", 503)
    if (
        connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        or connection.execute("PRAGMA foreign_key_check").fetchone()
    ):
        raise ServiceError("mailbox_store_corrupt", 503)


def body_path(root: Path, key: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", key) or key in {".", ".."}:
        raise ServiceError("mailbox_body_key_invalid", 503)
    path = root / key
    if path.is_symlink() or not path.is_file():
        raise ServiceError("mailbox_body_missing", 503)
    return path


def extract_mailbox(database: Path, bodies: Path, email: str, destination: Path) -> None:
    email = address(email)
    destination.mkdir(mode=0o700)
    target = destination / "imapsql.db"
    with connect(database) as source, closing(sqlite3.connect(target)) as isolated:
        validate(source)
        account = source.execute("SELECT id FROM users WHERE username=?", (email,)).fetchone()
        if account is None:
            raise ServiceError("account_not_found", 404)
        source.backup(isolated)
        isolated.execute("PRAGMA journal_mode=DELETE")
        isolated.execute("PRAGMA foreign_keys=ON")
        isolated.execute("PRAGMA secure_delete=ON")
        isolated.execute("DELETE FROM users WHERE id<>?", (account[0],))
        isolated.execute("DELETE FROM extKeys WHERE uid<>?", (account[0],))
        isolated.commit()
        isolated.execute("VACUUM")
        keys = [row[0] for row in isolated.execute("SELECT id FROM extKeys ORDER BY id")]
    target.chmod(0o600)
    copied = destination / "bodies"
    copied.mkdir(mode=0o700)
    for key in keys:
        shutil.copyfile(body_path(bodies, key), copied / key)
        (copied / key).chmod(0o600)
    with connect(target) as isolated:
        validate(isolated)
        if isolated.execute("SELECT username FROM users").fetchall()[0][0] != email:
            raise ServiceError("mailbox_extraction_failed", 503)


def _insert(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> int:
    if table not in COLUMNS or not set(row) <= COLUMNS[table]:
        raise ServiceError("invalid_mailbox_import", 503)
    names = ",".join('"' + name + '"' for name in row)
    placeholders = ",".join("?" for _ in row)
    cursor = connection.execute(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',  # noqa: S608 -- identifiers checked against fixed schema above
        tuple(row.values()),
    )
    return int(cursor.lastrowid or 0)


def import_mailbox(database: Path, bodies: Path, email: str, source: Path) -> None:
    """Standalone fixture/operator import; production uses import_rows atomically."""
    with connect(database) as live:
        keys = {row[0]: str(uuid.uuid4()) for row in _body_keys(source)}
        stage_bodies(live, bodies, source, keys)
        live.execute("BEGIN IMMEDIATE")
        import_rows(live, email, source, keys)


def _body_keys(source: Path) -> list[sqlite3.Row]:
    with connect(source / "imapsql.db") as isolated:
        validate(isolated)
        return isolated.execute("SELECT id FROM extKeys ORDER BY id").fetchall()


def body_mapping(source: Path, operation: str) -> dict[str, str]:
    namespace = uuid.UUID(operation)
    return {row[0]: str(uuid.uuid5(namespace, row[0])) for row in _body_keys(source)}


def stage_bodies(live: sqlite3.Connection, bodies: Path, source: Path,
                 keys: dict[str, str], *, resume: bool = False) -> None:
    """Only a durable operation plan may resume its own unreferenced body files."""
    metadata = bodies.stat()
    for old_key, new_key in keys.items():
        original = body_path(source / "bodies", old_key)
        target = bodies / new_key
        if live.execute("SELECT 1 FROM extKeys WHERE id=?", (new_key,)).fetchone():
            raise ServiceError('mailbox_import_body_conflict', 409)
        if target.is_symlink():
            raise ServiceError('mailbox_import_body_conflict', 409)
        if target.exists():
            if not resume:
                raise ServiceError('mailbox_import_body_conflict', 409)
            # The recorded plan proved the name unoccupied before the first write.
            # A killed copy may leave a short file; replace only this operation's name.
            target.unlink()
        with original.open('rb') as reader, target.open('xb') as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        target.chmod(0o600)
        os.chown(target, metadata.st_uid, metadata.st_gid)
        with original.open('rb') as reader, target.open('rb') as copied:
            if hashlib.file_digest(reader, 'sha256').digest() != hashlib.file_digest(
                copied, 'sha256',
            ).digest():
                raise ServiceError('mailbox_import_body_verification_failed', 503)
            os.fsync(copied.fileno())  # Persist owner/mode as well as the copied bytes.
    sync_directory(bodies)


def import_rows(live: sqlite3.Connection, email: str, source: Path,
                keys: dict[str, str]) -> int:
    email = address(email)
    with connect(source / "imapsql.db") as isolated:
        validate(live)
        validate(isolated)
        accounts = isolated.execute("SELECT * FROM users").fetchall()
        if len(accounts) != 1 or accounts[0]["username"] != email:
            raise ServiceError("invalid_mailbox_import", 503)
        if live.execute("SELECT 1 FROM users WHERE username=?", (email,)).fetchone():
            raise ServiceError("account_conflict", 409)
        account = dict(accounts[0])
        old_inbox = account.pop("inboxId")
        account.pop("id")
        account["inboxId"] = 0
        new_user = _insert(live, "users", account)
        folders = {}
        for original in isolated.execute("SELECT * FROM mboxes ORDER BY id"):
            row = dict(original)
            old_id = row.pop("id")
            row["uid"] = new_user
            folders[old_id] = _insert(live, "mboxes", row)
        if old_inbox not in folders:
            raise ServiceError("invalid_mailbox_import", 503)
        live.execute("UPDATE users SET inboxId=? WHERE id=?", (folders[old_inbox], new_user))
        for original in isolated.execute("SELECT * FROM extKeys ORDER BY id"):
            row = dict(original)
            row.update(id=keys[row['id']], uid=new_user)
            _insert(live, "extKeys", row)
        for original in isolated.execute("SELECT * FROM msgs ORDER BY mboxId,msgId"):
            row = dict(original)
            row["mboxId"] = folders[row["mboxId"]]
            if row["extBodyKey"] is not None:
                row["extBodyKey"] = keys[row["extBodyKey"]]
            _insert(live, "msgs", row)
        for original in isolated.execute("SELECT * FROM flags ORDER BY mboxId,msgId,flag"):
            row = dict(original)
            row["mboxId"] = folders[row["mboxId"]]
            _insert(live, "flags", row)
        validate(live)
        return new_user
