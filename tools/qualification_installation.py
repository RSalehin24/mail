"""Compare installed code and rendered units with the qualified host bundle."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from helper.maddy import LAUNCHER
from host.render import rendered_files
from tools.qualification_archive import ArtifactArchive

LIBRARY = Path("/usr/local/lib/mail-admin")
UNIT_TARGETS = {
    "journald-mail-admin.conf": "/etc/systemd/journald@mail-admin.conf",
    "docker-ordering.conf": "/etc/systemd/system/docker.service.d/mail-admin-ordering.conf",
    "mail-admin.tmpfiles": "/etc/tmpfiles.d/mail-admin.conf",
}


@dataclass(frozen=True)
class InstalledFile:
    sha256: str
    mode: int
    owner: int = 0
    group: int = 0

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{64}", self.sha256) is None or self.mode not in {
            0o600, 0o640, 0o644, 0o700, 0o750, 0o755
        } or any(type(value) is not int or value < 0 for value in (self.owner, self.group)):
            raise ValueError("Invalid qualified installed-file identity")


def installation_manifest(
    archive: ArtifactArchive, configuration: dict
) -> dict[str, InstalledFile]:
    files = {
        str(LIBRARY / name): InstalledFile(archive.digest(name), member.mode & 0o777)
        for name, member in archive.members.items() if member.isfile()
    }
    sources = {
        Path(name).name: archive.read(name).decode()
        for name, member in archive.members.items()
        if name.startswith("host/systemd/") and member.isfile()
    }
    for name, value in rendered_files(configuration, sources).items():
        if not name.startswith("mail-admin-external-monitor"):
            path = UNIT_TARGETS.get(name, "/etc/systemd/system/" + name)
            files[path] = InstalledFile(hashlib.sha256(value).hexdigest(), 0o644)
    files[LAUNCHER] = InstalledFile(
        archive.digest("host/maddy-launcher"), 0o755
    )
    _cutover_files(files, archive, configuration)
    return files


def _cutover_files(files: dict, archive: ArtifactArchive, configuration: dict) -> None:
    plan = configuration["cutover"]["files"]
    if not isinstance(plan, dict) or set(plan) != {
        "maddy_compose", "renewal_service", "renewal_timer", "renewal_script"
    }:
        raise ValueError("Complete qualified cutover files are required")
    script = archive.digest("host/renew-cert.sh")
    targets = {
        "renewal_service": "/etc/systemd/system/maddy-cert-renew.service",
        "renewal_timer": "/etc/systemd/system/maddy-cert-renew.timer",
        "renewal_script": "/opt/maddy/renew-cert.sh",
        "maddy_compose": configuration["consumers"]["maddy"]["compose_file"],
    }
    for role, record in plan.items():
        if record["target"] != targets[role]:
            raise ValueError("Qualified cutover file target does not match its role")
        expected = files.get(record["target"])
        digest = script if role == "renewal_script" else record["sha256"]
        if record["sha256"] != digest or (expected and expected.sha256 != digest):
            raise ValueError("Cutover file differs from the qualified host bundle")
        files[record["target"]] = InstalledFile(digest, record["mode"])


class InstalledTree:
    def __init__(self, root: Path = Path("/")) -> None:
        self.root = root.absolute()
        if self.root.resolve() != self.root:
            raise ValueError("Installed tree root must not be a symlink")

    def verify(self, manifest: dict[str, InstalledFile]) -> None:
        for target, expected in manifest.items():
            self._verify_file(target, expected)
        self._verify_source_set(manifest)

    def _verify_file(self, target: str, expected: InstalledFile) -> None:
        relative = Path(target)
        if not relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Installed manifest target must be an absolute safe path")
        path = self.root / relative.relative_to("/")
        if path.resolve() != path:
            raise ValueError("Installed artifact path contains a symlink")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as reader:
            before = os.fstat(reader.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError("Installed artifact is not a regular file")
            identity = InstalledFile(hashlib.file_digest(reader, "sha256").hexdigest(),
                                     stat.S_IMODE(before.st_mode), before.st_uid, before.st_gid)
            if identity != expected or _metadata(before) != _metadata(os.fstat(reader.fileno())):
                raise ValueError("Installed artifact content, permissions or ownership drift")
        if _metadata(before) != _metadata(path.lstat()):
            raise ValueError("Installed artifact changed during observation")

    def _verify_source_set(self, manifest: dict[str, InstalledFile]) -> None:
        prefix = str(LIBRARY) + "/"
        expected = {name for name in manifest if name.startswith(
            tuple(prefix + directory + "/" for directory in ("host", "helper", "shared"))
        )}
        observed = {
            "/" + str(path.relative_to(self.root))
            for directory in ("host", "helper", "shared")
            for path in (self.root / LIBRARY.relative_to("/") / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }
        if observed != expected:
            raise ValueError("Installed host source file set differs from the qualified bundle")


def _metadata(value: os.stat_result) -> tuple[int, ...]:
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)
