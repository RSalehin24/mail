"""Reviewed host-file replacements and a complete, immutable compatible rollback set."""

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from host.docker import Docker
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json, sync_directory


def file_digest(path: Path) -> str:
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


class ReleaseFiles:
    def __init__(
        self, plan: dict[str, Any], policy: tuple[int, tuple[Path, ...]]
    ) -> None:
        self.plan = plan
        self.owner, self.targets = policy
        if not plan or any(
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None
            or not isinstance(entry, dict)
            or set(entry) != {"target", "source", "sha256", "mode"}
            for name, entry in plan.items()
        ):
            raise ServiceError("invalid_release_file_plan", 503)
        for entry in plan.values():
            if (
                not isinstance(entry["target"], str)
                or not isinstance(entry["source"], str)
                or not Path(entry["target"]).is_absolute()
                or not Path(entry["source"]).is_absolute()
                or not isinstance(entry["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None
            ):
                raise ServiceError("invalid_release_file_plan", 503)
            self._permissions(entry["mode"], 0)
        selected = [entry["target"] for entry in plan.values()]
        if len(selected) != len(set(selected)) or set(selected) != {str(p) for p in self.targets}:
            raise ServiceError("release_file_targets_mismatch", 503)

    def read(self, path: Path) -> tuple[bytes, os.stat_result]:
        if path.resolve() != path.absolute():
            raise ServiceError("release_file_symlink", 503)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.owner
                or stat.S_IMODE(metadata.st_mode) & 0o022
            ):
                raise ServiceError("untrusted_release_file", 503)
            value = os.read(descriptor, 2 * 1024 * 1024 + 1)
            if len(value) > 2 * 1024 * 1024:
                raise ServiceError("release_file_too_large", 503)
            return value, metadata
        finally:
            os.close(descriptor)

    def replace(self, path: Path, content: tuple[bytes, int, int]) -> None:
        value, mode, group = content
        if path not in self.targets or path.parent.resolve() != path.parent:
            raise ServiceError("untrusted_release_target", 503)
        self._permissions(mode, group)
        descriptor, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
        with os.fdopen(descriptor, "wb") as writer:
            os.fchown(writer.fileno(), self.owner, group)
            os.fchmod(writer.fileno(), mode)
            writer.write(value)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)

    @staticmethod
    def _permissions(mode: int, group: int) -> None:
        if (
            type(mode) is not int
            or mode not in {0o600, 0o640, 0o644, 0o700, 0o750, 0o755}
            or type(group) is not int
            or group < 0
        ):
            raise ServiceError("unsafe_release_file_mode", 503)

    def candidate(self) -> None:
        prepared = []
        for record in self.plan.values():
            value, metadata = self.read(Path(record["source"]))
            if hashlib.sha256(value).hexdigest() != record["sha256"]:
                raise ServiceError("release_source_digest_mismatch", 503)
            self._permissions(record["mode"], metadata.st_gid)
            prepared.append((Path(record["target"]), (value, record["mode"], metadata.st_gid)))
        for target, content in prepared:
            self.replace(target, content)


class RollbackPackage:
    def __init__(self, directory: Path, files: ReleaseFiles, docker: Docker) -> None:
        self.directory, self.files, self.docker = directory, files, docker

    def create(self, release: dict[str, Any]) -> str:
        self._release(release)
        self.directory.mkdir(mode=0o700)
        records = self._capture_files()
        self._save_images(release)
        atomic_json(
            self.directory / "manifest.json",
            {
                "schema_version": 1,
                "release": release,
                "files": records,
                "images_sha256": self._archive_digest(),
            },
        )
        sync_directory(self.directory.parent)
        return file_digest(self.directory / "manifest.json")

    def _capture_files(self) -> dict[str, Any]:
        records = {}
        for name, entry in self.files.plan.items():
            target = Path(entry["target"])
            value, metadata = self.files.read(target)
            destination = self.directory / name
            with destination.open("xb") as writer:
                os.fchmod(writer.fileno(), 0o600)
                writer.write(value)
                writer.flush()
                os.fsync(writer.fileno())
            records[name] = {
                "target": str(target),
                "sha256": hashlib.sha256(value).hexdigest(),
                "mode": stat.S_IMODE(metadata.st_mode),
                "group": metadata.st_gid,
            }
        return records

    def _save_images(self, release: dict[str, Any]) -> Path:
        images = tuple(sorted({record["image_id"] for record in release["consumers"].values()}))
        self._images(images)
        archive = self.directory / "images.tar"
        self.docker.run(("image", "save", "--output", str(archive)) + images)
        os.chmod(archive, 0o600)
        with archive.open("rb") as reader:
            os.fsync(reader.fileno())
        return archive

    def _archive_digest(self) -> str:
        path = self.directory / "images.tar"
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != self.files.owner
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ServiceError("untrusted_rollback_image_archive", 503)
        return file_digest(path)

    def _images(self, images: tuple[str, ...]) -> None:
        for identity in images:
            if not isinstance(identity, str) or re.fullmatch(
                r"sha256:[0-9a-f]{64}", identity
            ) is None:
                raise ServiceError("rollback_image_identity_required", 503)
            observation = self.docker.inspect(identity)
            if (
                observation.get("Id") != identity
                or observation.get("Architecture") != "amd64"
                or observation.get("Os") != "linux"
            ):
                raise ServiceError("rollback_image_mismatch", 503)

    @staticmethod
    def _release(release: dict[str, Any]) -> None:
        consumers = release.get("consumers")
        identity = release.get("artifact_set_sha256")
        if (
            release.get("tls_layout") not in {"legacy", "generations"}
            or not isinstance(consumers, dict)
            or not consumers
            or not set(consumers) <= {"maddy", "mail-admin"}
            or "maddy" not in consumers
            or type(release.get("schema_version")) is not int
            or type(release.get("helper_protocol")) is not int
            or not isinstance(identity, str)
            or not re.fullmatch(r"[0-9a-f]{64}", identity)
        ):
            raise ServiceError("incomplete_rollback_set", 503)

    def verify(self, expected: str) -> dict[str, Any]:
        metadata = self.directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != self.files.owner
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ServiceError("untrusted_rollback_directory", 503)
        if file_digest(self.directory / "manifest.json") != expected:
            raise ServiceError("rollback_manifest_digest_mismatch", 503)
        manifest = protected_json(self.directory / "manifest.json", self.files.owner)
        self._release(manifest["release"])
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ServiceError("rollback_file_set_mismatch", 503)
        if set(manifest["files"]) != set(self.files.plan):
            raise ServiceError("rollback_file_set_mismatch", 503)
        for name, record in manifest["files"].items():
            self.files._permissions(record["mode"], record["group"])
            content, _ = self.files.read(self.directory / name)
            if (
                record["target"] != self.files.plan[name]["target"]
                or hashlib.sha256(content).hexdigest() != record["sha256"]
            ):
                raise ServiceError("rollback_file_digest_mismatch", 503)
        if self._archive_digest() != manifest["images_sha256"]:
            raise ServiceError("rollback_image_archive_mismatch", 503)
        return manifest

    def restore(self, expected: str) -> dict[str, Any]:
        manifest = self.verify(expected)
        self.docker.run(("image", "load", "--input", str(self.directory / "images.tar")))
        release = manifest["release"]
        self._images(tuple(record["image_id"] for record in release["consumers"].values()))
        for name, record in manifest["files"].items():
            content, _ = self.files.read(self.directory / name)
            self.files.replace(Path(record["target"]), (content, record["mode"], record["group"]))
        return dict(release)
