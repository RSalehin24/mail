"""Serialize capture through publication and retention, including manual invocations."""

import time
from pathlib import Path
from typing import Any

from host.backup_service import BackupService
from host.locks import HostLock, LockPolicy
from host.repository_backup import RepositoryBackup
from host.retention import BackupRetention
from host.rollout import operational_guard, rollout_directory
from shared.errors import ServiceError
from shared.files import protected_json

BACKUP_LOCK = Path("/run/lock/mail-admin-backup.lock")


class BackupOperations:
    def __init__(self, backup: BackupService, deployment: dict[str, Any]) -> None:
        self.backup, self.deployment = backup, deployment
        self.lock_policy = LockPolicy(deployment["inputs"]["lock_wait_seconds"])

    def baseline(self) -> dict[str, Any]:
        with HostLock(BACKUP_LOCK, self.lock_policy):
            return self.backup.transfer(self.backup.capture())

    def run(self) -> dict[str, Any]:
        with HostLock(BACKUP_LOCK, self.lock_policy):
            manifest = self.backup.transfer(self.backup.capture())
            protected = self._protected(manifest)
            if isinstance(self.backup, RepositoryBackup):
                self.backup.prune(protected)
            retention = BackupRetention(self.backup.paths.staging, (
                Path("/var/lib/mail-admin-backup/retention.json"), self.lock_policy,
                self.deployment["backup_local_retention_seconds"],
            ))
            retention.record(manifest)
            retention.prune((time.time(), protected))
            return manifest

    def verify(self) -> dict[str, Any]:
        with HostLock(BACKUP_LOCK, self.lock_policy):
            return self.backup.verify_latest()

    def baseline_manifest(self) -> dict[str, Any] | None:
        path = rollout_directory(self.deployment) / "baseline-manifest.json"
        return protected_json(path) if path.exists() else None

    def _protected(self, manifest: dict[str, Any]) -> set[str]:
        protected = {manifest["snapshot_id"], *self.deployment["rollback_snapshot_ids"]}
        baseline = self.baseline_manifest()
        if baseline is None:
            return protected
        try:
            operational_guard(self.deployment).checkpoint("G08")
        except (OSError, ServiceError):
            protected.add(baseline["snapshot_id"])
        return protected
