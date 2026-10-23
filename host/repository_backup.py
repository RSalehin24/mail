"""Reuse consistent capture with a native deduplicated, independently verified repository."""

import shutil
from pathlib import Path
from typing import Any

from host.backup import BackupPaths, SnapshotCapture, backup_manifest
from host.restic_repository import ResticRepository
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


class RepositoryBackup:
    def __init__(self, capture: SnapshotCapture, repository: ResticRepository) -> None:
        self.snapshot, self.repository = capture, repository

    @property
    def paths(self) -> BackupPaths:
        return self.snapshot.paths

    def capture(self) -> Path:
        state = self.repository.process.paths.state.resolve()
        for source in self.paths.sources.values():
            actual = source.resolve()
            if state.is_relative_to(actual) or actual.is_relative_to(state):
                raise ServiceError("backup_source_overlaps_repository_state", 503)
        return self.snapshot.capture()

    def transfer(self, snapshot: Path) -> dict[str, Any]:
        publication = self.repository.publish(snapshot)
        self.repository.verify(publication)
        manifest = backup_manifest(snapshot, publication, self.repository.clock())
        atomic_json(self.paths.manifest, manifest)
        return manifest

    def verify_latest(self) -> dict[str, Any]:
        record = protected_json(self.paths.manifest, self.repository.process.owner)
        try:
            self.repository.verify(record)
        except (OSError, ServiceError):
            record["available"] = False
            atomic_json(self.paths.manifest, record)
            raise
        record.update({"available": True, "availability_observed_at": self.repository.clock()})
        atomic_json(self.paths.manifest, record)
        return record

    def prune(self, protected: set[str]) -> None:
        snapshots = self.repository.snapshots()
        identities = {row.path.name: row.identifier for row in snapshots}
        if not protected <= identities.keys():
            raise ServiceError("protected_backup_snapshot_unavailable", 503)
        expired = self.repository.prune({identities[name] for name in protected})
        # Remote pruning cannot authorize removing any unrelated staging entry.
        for identifier in expired:
            path = next(row.path for row in snapshots if row.identifier == identifier)
            if path.parent.resolve() != self.paths.staging.resolve() or path.is_symlink():
                raise ServiceError("unsafe_staging_entry", 503)
            if path.exists():
                shutil.rmtree(path)
