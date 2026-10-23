"""Quiesced immutable capture, then encrypted off-host transfer and verified publication."""

import hashlib
import os
import shutil
import tarfile
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from helper.journal import Journal
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json, sync_directory

FULL_STATE_SOURCES = frozenset(
    {
        "maddy", "dashboard", "operator", "helper", "certificates", "host_tools",
        "units", "tmpfiles", "docker_ordering", "launcher", "alerts",
        "maddy_configuration", "maddy_compose", "tls", "acme", "acme_lib",
        "renewal_script", "release_artifacts",
    }
)


class SnapshotServices(Protocol):
    def running(self) -> list[str]: ...
    def pause(self, names: list[str]) -> None: ...
    def resume(self, names: list[str]) -> None: ...
    def inventories(self) -> dict[str, list[str]]: ...


class ArchiveTransfer(Protocol):
    def publish(self, archive: Path) -> str: ...
    def fetch(self, reference: str, destination: Path) -> None: ...


class ArchiveEncryption(Protocol):
    def encrypt(self, source: Path, destination: Path) -> None: ...


class CaptureScope(Protocol):
    @property
    def journal(self) -> Journal | None: ...
    def consistent(self) -> AbstractContextManager[None]: ...


@dataclass(frozen=True)
class BackupPaths:
    staging: Path
    manifest: Path
    sources: dict[str, Path]
    message_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class BackupDependencies:
    coordination: CaptureScope
    services: SnapshotServices
    encryption: ArchiveEncryption
    transfer: ArchiveTransfer
    clock: Callable[[], float]


@dataclass(frozen=True)
class CaptureDependencies:
    coordination: CaptureScope
    services: SnapshotServices
    clock: Callable[[], float]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SnapshotCapture:
    def __init__(self, paths: BackupPaths, dependencies: CaptureDependencies) -> None:
        self.paths, self.dependencies = paths, dependencies

    def capture(self) -> Path:
        self._validate_sources()
        snapshot = self.paths.staging / str(uuid.uuid4())
        snapshot.mkdir(mode=0o700)
        with self.dependencies.coordination.consistent():
            inventory = self.dependencies.services.inventories()
            journal = self.dependencies.coordination.journal
            if journal is not None:
                journal.synchronize(inventory, self.dependencies.clock())
            running = self.dependencies.services.running()
            try:
                self.dependencies.services.pause(running)
                self._copy(snapshot)
                cutoff = self.dependencies.clock()
                atomic_json(
                    snapshot / "snapshot.json",
                    {
                        "cutoff": cutoff,
                        "inventory": inventory,
                        "accounts": self._accounts(inventory, snapshot),
                        "previously_running": running,
                    },
                )
                sync_directory(snapshot)
            finally:
                self.dependencies.services.resume(running)
        return snapshot

    def _copy(self, snapshot: Path) -> None:
        self._validate_sources()
        for name, source in self.paths.sources.items():
            target = snapshot / name
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
            self._ownership(source, target)
        for root, _directories, files in os.walk(snapshot):
            for name in files:
                path = Path(root) / name
                if not path.is_symlink():
                    with path.open("rb") as reader:
                        os.fsync(reader.fileno())
            sync_directory(Path(root))

    def _validate_sources(self) -> None:
        staging = self.paths.staging.resolve()
        for name, source in self.paths.sources.items():
            if not name.replace("_", "").isalnum() or source.is_symlink() or not source.exists():
                raise ServiceError("backup_source_invalid", 503)
            actual = source.resolve()
            if actual.is_relative_to(staging) or staging.is_relative_to(actual):
                raise ServiceError("backup_source_overlaps_staging", 503)

    @staticmethod
    def _ownership(source: Path, target: Path) -> None:
        metadata = source.lstat()
        os.chown(target, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
        if source.is_dir():
            for root, directories, files in os.walk(source, followlinks=False):
                for name in directories + files:
                    original = Path(root) / name
                    metadata = original.lstat()
                    copied = target / original.relative_to(source)
                    os.chown(copied, metadata.st_uid, metadata.st_gid, follow_symlinks=False)

    def _accounts(self, inventory: dict[str, list[str]], snapshot: Path) -> dict[str, Any]:
        result = {}
        journal = self.dependencies.coordination.journal
        for email in set(inventory["credentials"]) | set(inventory["storage"]):
            incarnation = (
                journal.incarnation(email) if journal is not None else {"generation": None}
            )
            result[email] = {
                "generation": incarnation["generation"],
                "credentials": email in inventory["credentials"]
                and (snapshot / "maddy/credentials.db").is_file(),
                "storage": email in inventory["storage"]
                and (snapshot / "maddy/imapsql.db").is_file(),
                "messages": email in inventory["storage"] and self._messages_copied(snapshot),
            }
        return result

    def _messages_copied(self, snapshot: Path) -> bool:
        if not self.paths.message_paths:
            return False
        for relative in self.paths.message_paths:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ServiceError("unreviewed_message_path", 503)
            copied = snapshot / "maddy" / path
            if copied.is_symlink() or not copied.exists():
                return False
        return True


class Backup:
    def __init__(self, paths: BackupPaths, dependencies: BackupDependencies) -> None:
        self.paths, self.dependencies = paths, dependencies
        self.snapshot = SnapshotCapture(paths, CaptureDependencies(
            dependencies.coordination, dependencies.services, dependencies.clock,
        ))

    def capture(self) -> Path:
        return self.snapshot.capture()

    def verify_latest(self) -> dict[str, Any]:
        record = protected_json(self.paths.manifest)
        destination = self.paths.staging / (str(uuid.uuid4()) + ".verification")
        try:
            self._fetch_verified(record, destination)
            record["available"] = True
        except (OSError, ServiceError):
            record["available"] = False
            atomic_json(self.paths.manifest, record)
            raise
        record["availability_observed_at"] = self.dependencies.clock()
        atomic_json(self.paths.manifest, record)
        return record

    def _fetch_verified(self, record: dict[str, Any], destination: Path) -> None:
        try:
            self.dependencies.transfer.fetch(record["reference"], destination)
            if file_sha256(destination) != record["archive_sha256"]:
                raise ServiceError("off_host_archive_integrity_failed", 503)
        finally:
            destination.unlink(missing_ok=True)

    def transfer(self, snapshot: Path) -> dict[str, Any]:
        raw, encrypted = snapshot.with_suffix(".tar.gz"), snapshot.with_suffix(".encrypted")
        with tarfile.open(raw, "x:gz", compresslevel=6) as archive:
            archive.add(snapshot, arcname="snapshot", recursive=True)
        os.chmod(raw, 0o600)
        self.dependencies.encryption.encrypt(raw, encrypted)
        reference = self.dependencies.transfer.publish(encrypted)
        verified_copy = encrypted.with_suffix(".verified")
        self.dependencies.transfer.fetch(reference, verified_copy)
        digest = file_sha256(encrypted)
        if file_sha256(verified_copy) != digest:
            raise ServiceError("off_host_archive_integrity_failed", 503)
        manifest = backup_manifest(
            snapshot, {"archive_sha256": digest, "reference": reference}, self.dependencies.clock()
        )
        atomic_json(self.paths.manifest, manifest)
        return manifest


def backup_manifest(
    snapshot: Path, publication: dict[str, Any], observed_at: float,
) -> dict[str, Any]:
    import json

    metadata = json.loads((snapshot / "snapshot.json").read_text())
    return {
        **publication,
        "snapshot_id": snapshot.name,
        "cutoff": datetime.fromtimestamp(metadata["cutoff"], UTC).isoformat(),
        "verified": True,
        "available": True,
        "restore_passed": False,
        "availability_observed_at": observed_at,
        "accounts": metadata["accounts"],
    }
