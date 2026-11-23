"""One SQLite file for the helper's operation journal, recovery ledger and forwarding records.

Earlier versions kept these in journal.db, recovery.db and forwarding.db. The first open of
state.db copies their rows in one transaction (with a marker row per old file, committed
together with the rows) and only then renames each old file to <name>.migrated. A crash at any
point therefore neither duplicates nor loses rows: before the commit nothing happened, after it
the markers stop a second copy and the rename is simply retried. Old files are never deleted.
"""

import os
import sqlite3
import stat
import time
from pathlib import Path

from shared.errors import ServiceError

STATE_FILE = "state.db"
SCHEMA_VERSION = 1
MIGRATED_SUFFIX = ".migrated"
BUSY_TIMEOUT_SECONDS = 0.25

# The tables each old file contributes. recovery_jobs and recovery_checkpoints of recovery.db
# are not copied: no code reads them any more, and they stay in recovery.db.migrated.
LEGACY_TABLES = {
    "journal.db": ("operations", "incarnations"),
    "recovery.db": ("recovery_records", "recovery_audit"),
    "forwarding.db": ("metadata", "rules", "messages", "requests", "verifications", "audit"),
}

_SCHEMA = (
    "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied REAL NOT NULL)",
    "CREATE TABLE legacy_imports(name TEXT PRIMARY KEY, size INTEGER NOT NULL, "
    "modified_ns INTEGER NOT NULL)",
    "CREATE TABLE operations(id TEXT PRIMARY KEY, operation TEXT NOT NULL, "
    "email TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, "
    "state TEXT NOT NULL, phase TEXT, steps TEXT NOT NULL, "
    "preconditions TEXT NOT NULL, postconditions TEXT NOT NULL)",
    "CREATE TABLE incarnations(email TEXT PRIMARY KEY,generation TEXT NOT NULL, "
    "created REAL NOT NULL,present INTEGER NOT NULL)",
    """CREATE TABLE recovery_records (
        id TEXT PRIMARY KEY, email TEXT NOT NULL, state TEXT NOT NULL,
        retention TEXT NOT NULL, deleted_at INTEGER NOT NULL,
        deadline INTEGER, policy_revision INTEGER NOT NULL,
        snapshot TEXT, display_name TEXT NOT NULL, notes TEXT NOT NULL,
        updated_at INTEGER NOT NULL)""",
    """CREATE TABLE recovery_audit (
        id INTEGER PRIMARY KEY, created INTEGER NOT NULL, action TEXT NOT NULL,
        request_id TEXT NOT NULL, email TEXT NOT NULL, outcome TEXT NOT NULL)""",
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY,revision INTEGER NOT NULL)",
    """CREATE TABLE rules (email TEXT PRIMARY KEY,destination TEXT NOT NULL,
        enabled INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)""",
    """CREATE TABLE messages (email TEXT NOT NULL,body_key TEXT NOT NULL,
        destination TEXT NOT NULL,state TEXT NOT NULL,
        created REAL NOT NULL,updated REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',body BLOB,
        PRIMARY KEY(email,body_key,destination))""",
    "CREATE TABLE requests (id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL)",
    "CREATE TABLE verifications (destination TEXT PRIMARY KEY,state TEXT NOT NULL)",
    """CREATE TABLE audit (created REAL NOT NULL,action TEXT NOT NULL,
        email TEXT NOT NULL,request_id TEXT NOT NULL)""",
)


def prepare_state(path: Path) -> None:
    """Create, upgrade and import into state.db; safe to call on every open and concurrently."""
    _ensure_file(path)
    legacy = _legacy_files(path.parent)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                                 timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    try:
        for name, old in legacy.items():
            connection.execute(f"ATTACH DATABASE ? AS {_alias(name)}", (old.as_uri() + "?mode=rw",))  # noqa: S608 -- fixed alias
        _migrate(connection, legacy)
    except (sqlite3.Error, OSError) as error:
        raise ServiceError("state_unavailable", 503) from error
    finally:
        connection.close()
    for old in legacy.values():
        _retire(old)


def shared_state_path(first: Path, second: Path) -> Path:
    """The one state file two stores must share for their joint transaction to be atomic."""
    if first != second:
        raise ServiceError("state_storage_mismatch", 503)
    return first


def _alias(name: str) -> str:
    return "legacy_" + name.removesuffix(".db")


def _ensure_file(path: Path) -> None:
    if path.is_symlink():
        raise ServiceError("unsafe_state_file", 503)
    if not path.exists():
        _create_private_file(path)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != path.parent.stat().st_uid
    ):
        raise ServiceError("unsafe_state_file", 503)


def _create_private_file(path: Path) -> None:
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except FileExistsError:
        return
    directory_owner = path.parent.stat().st_uid
    if os.geteuid() == 0 and directory_owner != 0:
        # A root worker may be first; the dashboard user must still be able to open the file.
        os.chown(path, directory_owner, -1)


def _legacy_files(directory: Path) -> dict[str, Path]:
    found = {}
    for name in LEGACY_TABLES:
        old = directory / name
        if old.is_symlink() or (old.exists() and not old.is_file()):
            raise ServiceError("unsafe_state_migration_source", 503)
        if old.exists():
            found[name] = old
    return found


def _migrate(connection: sqlite3.Connection, legacy: dict[str, Path]) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = _applied_version(connection)
        if version > SCHEMA_VERSION:
            raise ServiceError("state_schema_unsupported", 503)
        if version < 1:
            for statement in _SCHEMA:
                connection.execute(statement)
        for name, old in legacy.items():
            _import_once(connection, name, old)
        if version < 1:
            connection.execute("INSERT INTO metadata VALUES(1,0) ON CONFLICT(id) DO NOTHING")
            connection.execute("INSERT INTO schema_migrations VALUES(?,?)",
                               (SCHEMA_VERSION, time.time()))
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _applied_version(connection: sqlite3.Connection) -> int:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone():
        return 0
    return int(connection.execute(
        "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
    ).fetchone()[0])


def _import_once(connection: sqlite3.Connection, name: str, old: Path) -> None:
    metadata = old.stat()
    seen = (metadata.st_size, metadata.st_mtime_ns)
    marker = connection.execute(
        "SELECT size,modified_ns FROM legacy_imports WHERE name=?", (name,)
    ).fetchone()
    if marker is not None:
        if tuple(marker) != seen:
            # The old file was written after its rows were copied (a rollback, then new writes).
            raise ServiceError("state_migration_source_changed", 503)
        return
    for table in LEGACY_TABLES[name]:
        _copy_table(connection, _alias(name), table)
    connection.execute("INSERT INTO legacy_imports VALUES(?,?,?)", (name, *seen))


def _copy_table(connection: sqlite3.Connection, alias: str, table: str) -> None:
    old_columns = [row[1] for row in connection.execute(f"PRAGMA {alias}.table_info({table})")]  # noqa: S608 -- fixed names
    if not old_columns:
        return  # An older old file may predate this table.
    new_columns = {row[1] for row in connection.execute(f"PRAGMA main.table_info({table})")}  # noqa: S608 -- fixed names
    if not set(old_columns) <= new_columns:
        raise ServiceError("state_migration_schema_mismatch", 503)
    columns = ",".join(old_columns)  # Each is a verified column of our own schema.
    expected = _count(connection, f"{alias}.{table}")
    before = _count(connection, f"main.{table}")
    connection.execute(
        f"INSERT INTO main.{table}({columns}) SELECT {columns} FROM {alias}.{table}"  # noqa: S608 -- fixed names
    )
    if _count(connection, f"main.{table}") - before != expected:
        raise ServiceError("state_migration_count_mismatch", 503)


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # noqa: S608 -- fixed names


def _retire(old: Path) -> None:
    """Keep the old file for rollback; leave it alone if a previous rollback copy is there."""
    retired = old.with_name(old.name + MIGRATED_SUFFIX)
    if retired.exists():
        return
    try:
        old.rename(retired)
    except FileNotFoundError:
        return  # Another process finished the rename first.
