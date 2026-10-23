"""Protected bounded reads and durable atomic host records."""

import json
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from shared.errors import ServiceError


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
    _atomic_json(path, value, group)


def _atomic_json(path: Path, value: dict[str, Any], group: int | None) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".staging-", dir=path.parent)
    with os.fdopen(descriptor, "w") as stream:
        if group is not None:
            os.fchown(stream.fileno(), -1, group)
            os.fchmod(stream.fileno(), 0o640)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)
