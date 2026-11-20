"""Identity check for the owned, software-emulated amd64 Linux guest."""

import os
import platform
import uuid
from pathlib import Path


def require_fixture_guest(instance: str) -> None:
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Lifecycle checks require root in the disposable amd64 Linux guest")
    identifier = str(uuid.UUID(instance))
    observed = Path("/var/lib/cloud/data/instance-id").read_text().strip()
    if instance != identifier or observed != identifier:
        raise RuntimeError("The disposable guest identity does not match")
