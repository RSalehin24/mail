"""Bounded metadata reads from frozen artifacts, without extracting or executing them."""

import hashlib
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_MEMBERS = 20000
MAX_METADATA_BYTES = 2 * 1024 * 1024


class ArtifactArchive:
    def __init__(self, path: Path) -> None:
        self.archive = tarfile.open(path, "r:*")
        self.members: dict[str, tarfile.TarInfo] = {}
        try:
            for member in self.archive:
                self._add(member)
        except BaseException:
            self.archive.close()
            raise

    def _add(self, member: tarfile.TarInfo) -> None:
        name = PurePosixPath(member.name)
        if (
            str(name) != member.name or name.is_absolute() or ".." in name.parts
            or member.name in self.members or len(self.members) >= MAX_MEMBERS
            or not (member.isfile() or member.isdir())
        ):
            raise ValueError("Unsafe or duplicate artifact archive member")
        self.members[member.name] = member

    def close(self) -> None:
        self.archive.close()

    def __enter__(self) -> "ArtifactArchive":
        return self

    def __exit__(self, *arguments: object) -> None:
        self.close()

    def read(self, name: str) -> bytes:
        member = self.members[name]
        if not member.isfile() or member.size > MAX_METADATA_BYTES:
            raise ValueError("Invalid or oversized artifact metadata")
        with self.archive.extractfile(member) as reader:
            value = reader.read(MAX_METADATA_BYTES + 1)
        if len(value) != member.size:
            raise ValueError("Truncated artifact metadata")
        return value

    def json(self, name: str) -> object:
        from shared.files import _unique_object

        return json.loads(self.read(name), object_pairs_hook=_unique_object)

    def digest(self, name: str) -> str:
        member = self.members[name]
        if not member.isfile():
            raise ValueError("Artifact member is not a file")
        with self.archive.extractfile(member) as reader:
            return hashlib.file_digest(reader, "sha256").hexdigest()


@dataclass(frozen=True)
class ImageIdentity:
    manifest: str | None
    configuration: str

    @property
    def allowed_ids(self) -> set[str]:
        return {value for value in (self.manifest, self.configuration) if value is not None}


def _blob(archive: ArtifactArchive, descriptor: dict) -> str:
    digest = descriptor.get("digest", "")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        raise ValueError("Unsupported image descriptor")
    path = "blobs/sha256/" + digest.removeprefix("sha256:")
    if archive.digest(path) != digest[7:] or archive.members[path].size != descriptor["size"]:
        raise ValueError("Image descriptor content mismatch")
    return path


def _oci_identity(archive: ArtifactArchive) -> ImageIdentity:
    index = archive.json("index.json")
    manifests = index["manifests"]
    # A Docker load/save roundtrip can omit this optional OCI descriptor field.
    # The hashed image configuration below must still establish amd64 and its user.
    expected = {"architecture": "amd64", "os": "linux"}
    if len(manifests) != 1 or manifests[0].get("platform", expected) != expected:
        raise ValueError("Export exactly one Linux/amd64 image")
    manifest = archive.json(_blob(archive, manifests[0]))
    configuration = _blob(archive, manifest["config"])
    _runtime_configuration(archive.json(configuration))
    return ImageIdentity(manifests[0]["digest"], manifest["config"]["digest"])


def _runtime_configuration(value: dict) -> None:
    if (
        value.get("architecture") != "amd64" or value.get("os") != "linux"
        or value.get("config", {}).get("User") != "10001:10001"
    ):
        raise ValueError("Qualified dashboard must run as 10001:10001 on Linux/amd64")


def image_identity(path: Path) -> ImageIdentity:
    with ArtifactArchive(path) as archive:
        if "index.json" in archive.members:
            return _oci_identity(archive)
        manifests = archive.json("manifest.json")
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise ValueError("Export exactly one dashboard image")
        configuration = manifests[0]["Config"]
        _runtime_configuration(archive.json(configuration))
        return ImageIdentity(None, "sha256:" + archive.digest(configuration))
