"""SFTP-backed scp with pinned host trust; verification downloads the encrypted bytes."""

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from helper.process import Command, execute
from host.backup import ArchiveTransfer
from host.r2 import r2_transfer
from shared.errors import ServiceError


def archive_transfer(deployment: dict[str, Any]) -> ArchiveTransfer:
    destination = deployment["inputs"]["backup_destination"]
    if isinstance(destination, str) and destination.startswith("s3://"):
        return r2_transfer(deployment)
    return ScpTransfer(
        destination,
        (Path(deployment["backup_ssh_key"]), Path(deployment["backup_known_hosts"])),
    )


class ScpTransfer:
    def __init__(self, destination: str, credentials: tuple[Path, Path]) -> None:
        endpoint = urlsplit(destination)
        user, host = endpoint.username or "", endpoint.hostname or ""
        path = endpoint.path
        if (
            endpoint.scheme != "ssh"
            or endpoint.password
            or endpoint.port not in {None, 22}
            or not re.fullmatch(r"[a-zA-Z0-9_-]+", user)
            or not re.fullmatch(r"[a-zA-Z0-9.-]+", host)
            or not re.fullmatch(r"/[a-zA-Z0-9_./-]+", path)
            or ".." in path.split("/")
        ):
            raise ServiceError("invalid_backup_destination", 503)
        self.base = f"{user}@{host}:{path.rstrip('/')}"
        key, known_hosts = credentials
        self.prefix = (
            "/usr/bin/scp",
            "-B",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + str(known_hosts),
            "-i",
            str(key),
        )

    def _copy(self, arguments: tuple[str, str]) -> None:
        response = execute(Command(self.prefix + arguments, timeout=300))
        if response.code:
            raise ServiceError("off_host_transfer_failed", 503)

    def publish(self, archive: Path) -> str:
        reference = self.base + "/" + archive.name
        self._copy((str(archive), reference))
        return reference

    def fetch(self, reference: str, destination: Path) -> None:
        if not reference.startswith(self.base + "/") or "/" in reference[len(self.base) + 1 :]:
            raise ServiceError("untrusted_archive_reference", 503)
        if destination.exists():
            raise ServiceError("archive_target_exists", 409)
        self._copy((reference, str(destination)))
