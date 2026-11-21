"""Image identity and lock-file checks shared by the root tools."""

import os
import stat
from pathlib import Path
from typing import Any

from host.docker import Docker
from shared.errors import ServiceError


def validate_image(deployment: dict[str, Any], docker: Docker) -> None:
    image = docker.inspect(deployment["dashboard_image_id"])
    if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise ServiceError("dashboard_architecture_mismatch", 503)
    if str(image.get("Config", {}).get("User")) != "10001:10001":
        raise ServiceError("dashboard_image_user_mismatch", 503)


def provision_lock(path: Path, owner: int = 0) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            if owner != os.geteuid():
                os.fchown(descriptor, owner, -1)
        finally:
            os.close(descriptor)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ServiceError("unsafe_lock_file", 503)
