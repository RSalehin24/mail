"""Explicit installation creation; never called from the public application."""

import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from backend.database import Database
from backend.secrets import SecretKeys
from shared.errors import ServiceError
from shared.files import atomic_json, sync_directory
from shared.runtime_policy import RUNTIME_POLICY_NAME
from shared.validation import SCHEMA_VERSION


@dataclass(frozen=True)
class InstallationPaths:
    database: Path
    secrets: Path
    marker: Path


def create_installation(paths: InstallationPaths) -> tuple[Database, SecretKeys]:
    required = (
        paths.database,
        paths.marker,
        paths.secrets / "encryption.key",
        paths.secrets / "verifier.key",
        paths.secrets / RUNTIME_POLICY_NAME,
    )
    if any(path.exists() or path.is_symlink() for path in required):
        raise ServiceError("installation_already_exists", 409)
    identity = str(uuid.uuid4())
    keys = SecretKeys(secrets.token_bytes(32), secrets.token_bytes(32))
    database = Database.provision(paths.database, identity)
    _write_key(paths.secrets / "encryption.key", keys.encryption)
    _write_key(paths.secrets / "verifier.key", keys.verifier)
    atomic_json(
        paths.marker,
        {
            "installation_id": identity,
            "schema_version": SCHEMA_VERSION,
            "encryption_sha256": hashlib.sha256(keys.encryption).hexdigest(),
            "verifier_sha256": hashlib.sha256(keys.verifier).hexdigest(),
        },
    )
    atomic_json(
        paths.secrets / RUNTIME_POLICY_NAME,
        {"installation_id": identity, "intent": "expected_running"},
    )
    sync_directory(paths.database.parent)
    return database, keys


def _write_key(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)
