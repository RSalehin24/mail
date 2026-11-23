"""Copy state while Maddy and the dashboard keep running.

SQLite databases are snapshotted through the online backup API, so a WAL database
written during the copy is still one consistent commit. Spool files that Maddy
removes between listing and copying are skipped, because they only describe
deliveries that finished while the backup ran.
"""

import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from host.mailbox_store import body_path, connect, linked_body_keys
from shared.errors import ServiceError

SQLITE_HEADER = b"SQLite format 3\x00"
SIDE_FILE_SUFFIXES = ("-wal", "-shm", "-journal")
MAIL_COPY_ATTEMPTS = 3
MAIL_INDEX = "imapsql.db"
MAIL_BODIES = "messages"


def is_sqlite_database(path: Path) -> bool:
    if not path.name.endswith(".db"):
        return False
    with path.open("rb") as reader:
        return reader.read(len(SQLITE_HEADER)) == SQLITE_HEADER


def snapshot_database(source: Path, target: Path) -> None:
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as live, \
            closing(sqlite3.connect(target)) as copy:
        live.backup(copy)
        # A standalone file leaves no -wal/-shm beside the snapshot when it is opened later.
        copy.execute("PRAGMA journal_mode=DELETE")
    shutil.copystat(source, target)


def copy_live_file(source: str, target: str) -> None:
    try:
        if is_sqlite_database(Path(source)):
            snapshot_database(Path(source), Path(target))
        else:
            shutil.copy2(source, target)
    except FileNotFoundError:
        Path(target).unlink(missing_ok=True)


def _database_side_files(names: list[str]) -> set[str]:
    return {
        name for name in names
        if name.endswith(SIDE_FILE_SUFFIXES) and name.rsplit("-", 1)[0].endswith(".db")
    }


def copy_live_tree(source: Path, target: Path, *, held_back: frozenset[str] = frozenset()) -> None:
    """Copy a tree; `held_back` names at its top level are left for the caller."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        top_level = names if Path(directory) == source else []
        return _database_side_files(names) | held_back.intersection(top_level)

    shutil.copytree(
        source, target, symlinks=True, copy_function=copy_live_file,
        ignore=ignore, dirs_exist_ok=True,
    )


def has_mail_store(source: Path) -> bool:
    return (source / MAIL_INDEX).is_file() and (source / MAIL_BODIES).is_dir()


def _copy_mail_attempt(source: Path, target: Path) -> None:
    # The index goes first: a body added afterwards is harmless, one removed is detected.
    target.mkdir()
    snapshot_database(source / MAIL_INDEX, target / MAIL_INDEX)
    copy_live_tree(source, target, held_back=frozenset({MAIL_INDEX, MAIL_BODIES}))
    copy_live_tree(source / MAIL_BODIES, target / MAIL_BODIES)


def _bodies_missing(target: Path) -> bool:
    with connect(target / MAIL_INDEX) as index:
        keys = linked_body_keys(index)
    try:
        for key in keys:
            body_path(target / MAIL_BODIES, key)
    except ServiceError:
        return True
    return False


def copy_mail_store(source: Path, target: Path) -> None:
    for _attempt in range(MAIL_COPY_ATTEMPTS):
        shutil.rmtree(target, ignore_errors=True)
        _copy_mail_attempt(source, target)
        if not _bodies_missing(target):
            return
    raise ServiceError("backup_capture_inconsistent", 503)


def copy_source(source: Path, target: Path) -> None:
    if not source.is_dir():
        copy_live_file(str(source), str(target))
    elif has_mail_store(source):
        copy_mail_store(source, target)
    else:
        copy_live_tree(source, target)


def match_ownership(source: Path, target: Path) -> None:
    """Mirror owner and group; entries that vanished or were skipped have nothing to mirror."""
    for original in [source, *source.rglob("*")]:
        try:
            metadata = original.lstat()
            os.chown(
                target / original.relative_to(source), metadata.st_uid, metadata.st_gid,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
