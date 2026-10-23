"""Prune only recorded staging entries after their verified off-host publication."""

import re
import shutil
from pathlib import Path
from typing import Any

from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json, sync_directory


class BackupRetention:
    def __init__(self, directory: Path, state: tuple[Path, LockPolicy, float]) -> None:
        self.directory = directory
        self.index, self.lock_policy, self.seconds = state
        if self.seconds <= 0:
            raise ServiceError("backup_retention_required", 503)

    def record(self, manifest: dict[str, Any]) -> None:
        with HostLock(self.index.with_suffix(".lock"), self.lock_policy):
            index = protected_json(self.index, self.lock_policy.owner)
            identifier = manifest["snapshot_id"]
            if (
                not re.fullmatch(r"[0-9a-f-]{36}", identifier)
                or manifest.get("verified") is not True
            ):
                raise ServiceError("unverified_retention_entry", 503)
            index[identifier] = {
                "reference": manifest["reference"],
                "recorded": manifest["availability_observed_at"],
            }
            atomic_json(self.index, index)

    def prune(self, observation: tuple[float, set[str]]) -> None:
        now, protected = observation
        with HostLock(self.index.with_suffix(".lock"), self.lock_policy):
            index = protected_json(self.index, self.lock_policy.owner)
            expired = [
                identifier
                for identifier, entry in index.items()
                if identifier not in protected and now - entry["recorded"] > self.seconds
            ]
            for identifier in expired:
                self._remove(identifier)
                del index[identifier]
            atomic_json(self.index, index)
            sync_directory(self.directory)

    def _remove(self, identifier: str) -> None:
        if not re.fullmatch(r"[0-9a-f-]{36}", identifier):
            raise ServiceError("invalid_retention_entry", 503)
        for suffix in ("", ".tar", ".tar.gz", ".encrypted", ".verified", "-cipher"):
            path = self.directory / (identifier + suffix)
            if path.is_symlink():
                raise ServiceError("unsafe_staging_entry", 503)
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
