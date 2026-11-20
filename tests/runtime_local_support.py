"""Dashboard and Maddy as one unprivileged user: no helper socket, no Docker access."""

import json
import os
import re
from pathlib import Path

from helper.journal import Journal
from helper.maddy import DockerMaddy
from tests.maddy_support import RealMaddyFixture
from tests.runtime_support import RuntimeFixture
from tools.resources import MADDY_RUNTIME_TMPFS_BYTES

SERVICE_ID = 10001
HELPER_SOCKET_TARGET = "/run/mail-admin-helper"
STATE_TARGET = "/var/lib/mail-admin-helper"
LOCK_TARGET = "/run/lock/mail-admin-maddy.lock"
HOST_RECORDS_TARGET = "/run/mail-admin-host/"
MADDY_RUNTIME_TARGET = "/run/maddy"
OPERATION_TIMEOUT_SECONDS = 5


def maddy_image_reference():
    """The pinned image the runtime image copies its Maddy binary from, so both match."""
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    match = re.search(r"^FROM (\S+) AS maddy-binary$", dockerfile.read_text(), re.MULTILINE)
    if match is None:
        raise RuntimeError("The Dockerfile must name the pinned maddy-binary stage")
    return match[1]


def own_tree(path):
    os.chown(path, SERVICE_ID, SERVICE_ID)
    for root, directories, files in os.walk(path):
        for name in directories + files:
            os.chown(Path(root, name), SERVICE_ID, SERVICE_ID)


def maddy_runtime_tmpfs():
    return (f"{MADDY_RUNTIME_TARGET}:size={MADDY_RUNTIME_TMPFS_BYTES},mode=0700,"
            f"uid={SERVICE_ID},gid={SERVICE_ID}")


class UnprivilegedMaddyFixture(RealMaddyFixture):
    """The production Maddy image, as the dashboard's user with every capability dropped."""

    additional_arguments = (
        "--user", f"{SERVICE_ID}:{SERVICE_ID}", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
    )
    maddy_class = DockerMaddy

    def image_reference(self):
        return maddy_image_reference()

    def runtime_tmpfs(self):
        return maddy_runtime_tmpfs()

    def launch(self):
        for path in (self.data, self.configuration, self.tls):
            own_tree(path)
        return super().launch()


class LocalHostRuntime(RuntimeFixture):
    """The runtime image with LOCAL_HOST=true running the Maddy CLI over Maddy's own files."""

    writable_targets = frozenset({"/app/data", "/data", STATE_TARGET, LOCK_TARGET})
    extra_environment = ("LOCAL_HOST=true",)
    extra_arguments = ("--tmpfs", maddy_runtime_tmpfs())

    def __init__(self, maddy):
        super().__init__()
        self.maddy = maddy
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.lock = self.root / "maddy.lock"
        self.lock.touch(mode=0o600)
        for path in (self.state, self.lock):
            os.chown(path, SERVICE_ID, SERVICE_ID)
        self.records = {name: self.host_record(name, content)
                        for name, content in self.record_contents().items()}

    def record_contents(self):
        return {
            "deployment.json": {
                "inputs": {
                    "lock_wait_seconds": OPERATION_TIMEOUT_SECONDS,
                    "operation_timeout_seconds": OPERATION_TIMEOUT_SECONDS,
                },
                "backup_sources": {},
                "maddy_cli_version": "0.9.6",
                "state_owner_uid": SERVICE_ID,
            },
            "helper-policy.json": {},
            "backup-manifest.json": {},
        }

    def host_record(self, name, content):
        """Root-owned and group-readable, as the deletion guard requires of host records."""
        path = self.root / name
        path.write_text(json.dumps(content))
        os.chown(path, 0, SERVICE_ID)
        path.chmod(0o640)
        return path

    def bind_sources(self):
        base = tuple(item for item in super().bind_sources() if item[1] != HELPER_SOCKET_TARGET)
        local = (
            (self.maddy.data, "/data"),
            (self.maddy.configuration, "/data/maddy.conf"),
            (self.maddy.tls, "/data/tls"),
            (self.state, STATE_TARGET),
            (self.lock, LOCK_TARGET),
        ) + tuple((path, HOST_RECORDS_TARGET + name) for name, path in self.records.items())
        return base + local

    def journal(self):
        return Journal(self.state / "journal.db", SERVICE_ID)
