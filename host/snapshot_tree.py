"""Content and ownership verification for native deduplicated snapshots and restores."""

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from shared.errors import ServiceError

VERIFICATION_FILE = "snapshot-verification.json"
MAX_ENTRIES = 100_000


@dataclass(frozen=True)
class SnapshotTree:
    sha256: str
    file_bytes: int
    entries: int


def snapshot_tree(directory: Path) -> SnapshotTree:
    digest, file_bytes, entries = hashlib.sha256(), 0, 0
    for path in _bounded_paths(directory):
        metadata = path.lstat()
        relative = str(path.relative_to(directory))
        record = {"path": relative, "mode": stat.S_IMODE(metadata.st_mode),
                  "uid": metadata.st_uid, "gid": metadata.st_gid}
        identity, size = _file_identity(path, directory)
        record.update(identity)
        digest.update(json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        file_bytes += size
        entries += 1
        if entries > MAX_ENTRIES:
            raise ServiceError("backup_entry_limit_exceeded", 503)
    return SnapshotTree(digest.hexdigest(), file_bytes, entries)


def _bounded_paths(directory: Path) -> list[Path]:
    result = []
    for path in directory.rglob("*"):
        if path == directory / VERIFICATION_FILE:
            continue
        result.append(path)
        if len(result) > MAX_ENTRIES:
            raise ServiceError("backup_entry_limit_exceeded", 503)
    return sorted(result)


def _file_identity(path: Path, directory: Path) -> tuple[dict[str, str], int]:
    metadata = path.lstat()
    if stat.S_ISREG(metadata.st_mode):
        with path.open("rb") as reader:
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
        return {"type": "file", "sha256": digest}, metadata.st_size
    if stat.S_ISDIR(metadata.st_mode):
        return {"type": "directory"}, 0
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        if Path(target).is_absolute() or not path.resolve().is_relative_to(directory.resolve()):
            raise ServiceError("backup_symlink_escapes_snapshot", 503)
        return {"type": "symlink", "target": target}, 0
    raise ServiceError("unsupported_backup_entry", 503)
