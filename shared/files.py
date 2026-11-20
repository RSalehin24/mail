"""Protected bounded reads and durable atomic host records."""

import json
import math
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.errors import ServiceError


def state_owner_uid(deployment: dict[str, Any]) -> int:
    """Owner of the files shared with the dashboard (locks, journal); root unless configured."""
    owner = deployment.get("state_owner_uid", 0)
    if type(owner) is not int or owner < 0:
        raise ServiceError("invalid_state_owner", 503)
    return owner


def protected_bytes(path: Path, owner: int = 0) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner:
            raise ServiceError("untrusted_file", 503)
        if stat.S_IMODE(metadata.st_mode) not in {0o600, 0o640}:
            raise ServiceError("unsafe_file_mode", 503)
        value = os.read(descriptor, 2 * 1024 * 1024 + 1)
        if len(value) > 2 * 1024 * 1024:
            raise ServiceError("record_too_large", 503)
        return value
    finally:
        os.close(descriptor)


def protected_json(path: Path, owner: int = 0) -> dict[str, Any]:
    try:
        value = json.loads(
            protected_bytes(path, owner),
            object_pairs_hook=_unique_object,
            parse_constant=_finite_number,
            parse_float=_finite_number,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ServiceError("invalid_host_record", 503) from error
    if not isinstance(value, dict):
        raise ServiceError("invalid_host_record", 503)
    return value


def _finite_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite host record number")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate host record field")
        result[key] = value
    return result


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_json(path, value, None)


def atomic_group_json(path: Path, value: dict[str, Any], group: int) -> None:
    def share_with_group(descriptor: int) -> None:
        os.fchown(descriptor, -1, group)
        os.fchmod(descriptor, 0o640)

    _atomic_json(path, value, share_with_group)


def atomic_owned_json(path: Path, value: dict[str, Any], owner: int) -> None:
    """Hand the file to its reader before it appears, so no root-only version is ever visible."""
    _atomic_json(path, value, lambda descriptor: os.fchown(descriptor, owner, -1))


def _keep_sharing(path: Path, descriptor: int) -> None:
    """A replaced file keeps its group and mode, so a one-time grant to a reader survives rewrites.

    New files and files never shared stay as the caller makes them (0600); nothing is widened.
    """
    try:
        previous = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISREG(previous.st_mode):
        os.fchown(descriptor, -1, previous.st_gid)
        os.fchmod(descriptor, stat.S_IMODE(previous.st_mode))


def _atomic_json(
    path: Path, value: dict[str, Any], prepare: Callable[[int], None] | None
) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    with os.fdopen(descriptor, "w") as stream:
        _keep_sharing(path, stream.fileno())
        if prepare is not None:
            prepare(stream.fileno())
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)
