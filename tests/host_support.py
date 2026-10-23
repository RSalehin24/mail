"""Standard-library access to the real helper in the owned, software-emulated guest."""

import json
import os
import platform
import re
import sqlite3
import subprocess
import time
import uuid
from contextlib import closing
from pathlib import Path

from helper.journal import Journal
from shared.files import protected_json

CONTEXT = Path("/root/mail-admin-fixture/host-test-context.json")
SOCKET_DIRECTORY = Path("/run/mail-admin-helper")
SOCKET = SOCKET_DIRECTORY / "control.sock"
HELPER_UNIT = "mail-admin-helper.service"
CONSUMER_PROGRAM = """import signal,time
def stop(number,frame):
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
time.sleep(86400)
"""
CLIENT = """import json,sys
from pathlib import Path
from backend.helper_client import HelperClient
from shared.errors import ServiceError
try:
 result=HelperClient(Path('/run/mail-admin-helper/control.sock'),5).call(json.load(sys.stdin))
 print(json.dumps({'accepted':True,'result':result}))
except ServiceError as error:
 print(json.dumps({'accepted':False,'error':error.code,'status':error.status}))
"""


def require_fixture_guest(instance: str) -> None:
    if os.geteuid() != 0 or platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("Lifecycle checks require root in the disposable amd64 Linux guest")
    identifier = str(uuid.UUID(instance))
    observed = Path("/var/lib/cloud/data/instance-id").read_text().strip()
    if instance != identifier or observed != identifier:
        raise RuntimeError("The disposable guest identity does not match")


def require_guest(record: dict) -> None:
    require_fixture_guest(record['instance_id'])
    identifier = record['instance_id']
    if record.get("scope") != "mail-admin-local-mock":
        raise RuntimeError("The disposable guest scope does not match")
    if record.get("consumer_name") != "mail-admin-fixture-consumer-" + identifier:
        raise RuntimeError("Invalid disposable consumer name")
    for field in ("consumer_id", "runtime_image_id"):
        prefix = "sha256:" if field == "runtime_image_id" else ""
        if re.fullmatch(prefix + "[0-9a-f]{64}", record.get(field, "")) is None:
            raise RuntimeError("Invalid disposable container identity")
    uuid.UUID(record["request_id"])
    if record.get("email") != "helper-lifecycle@rsalehin24.me":
        raise RuntimeError("Only the synthetic lifecycle mailbox is allowed")


class GuestHelper:
    def __init__(self, record: dict) -> None:
        require_guest(record)
        self.record = record
        self.journal = Journal(Path("/var/lib/mail-admin-helper/journal.db"))
        if self.journal.unresolved():
            raise RuntimeError("Unresolved operations block controlled lifecycle checks")
        self.verify_consumer()

    @classmethod
    def load(cls) -> "GuestHelper":
        return cls(protected_json(CONTEXT))

    def command(self, arguments: tuple[str, ...]) -> str:
        result = subprocess.run(  # noqa: S603 -- fixed fixture argv after guest identity checks.
            arguments, capture_output=True, text=True, check=False, timeout=150
        )
        if result.returncode:
            raise RuntimeError("Fixture command failed: " + result.stderr[-2000:])
        return result.stdout.strip()

    def verify_consumer(self) -> None:
        observed = json.loads(self.command(("docker", "inspect", self.record["consumer_name"])))[0]
        allowed = (
            observed["Id"] == self.record["consumer_id"]
            and observed["Image"] == self.record["runtime_image_id"]
            and observed["Config"]["User"] == "10001:10001"
            and observed["Config"]["Labels"].get("mail-admin.fixture") == "true"
            and observed["HostConfig"]["ReadonlyRootfs"]
            and observed["HostConfig"]["NetworkMode"] == "none"
            and not observed["HostConfig"]["Privileged"]
            and observed["State"]["Running"]
            and observed["HostConfig"]["CapDrop"] == ["ALL"]
            and "no-new-privileges:true" in observed["HostConfig"]["SecurityOpt"]
        )
        mounts = [(m["Type"], m["Source"], m["Destination"], m["RW"])
                  for m in observed["Mounts"]]
        if not allowed or mounts != [("bind", str(SOCKET_DIRECTORY), str(SOCKET_DIRECTORY), False)]:
            raise RuntimeError("The exact restricted non-root fixture consumer is required")

    def call(self, request: dict, identity: str = "10001:10001") -> dict:
        result = subprocess.run(  # noqa: S603 -- fixed container; JSON data goes only on stdin.
            ("docker", "exec", "--user", identity, "-i", self.record["consumer_name"],
             "python", "-c", CLIENT), input=json.dumps(request), capture_output=True,
            text=True, check=False, timeout=30,
        )
        if result.returncode:
            raise RuntimeError("Fixture client failed: " + result.stderr[-1000:])
        return dict(json.loads(result.stdout))

    def outcome(self) -> dict:
        response = self.call({"operation": "outcome", "request_id": self.record["request_id"]})
        return dict(response["result"]["outcome"]) if response.get("accepted") else response

    def ready(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.outcome().get("state") == "complete":
                return
            time.sleep(0.1)
        raise RuntimeError("Helper did not recover within fifteen seconds")

    def consumer_inode(self) -> int:
        code = "from pathlib import Path; print(Path('/run/mail-admin-helper').stat().st_ino)"
        return int(self.command(("docker", "exec", self.record["consumer_name"],
                                 "python", "-c", code)))

    def credentials(self) -> list[tuple]:
        path = "file:/opt/maddy/data/credentials.db?mode=ro"
        with closing(sqlite3.connect(path, uri=True)) as db:
            return db.execute("SELECT * FROM passwords").fetchall()

    def helper_pid(self) -> str:
        return self.command(("systemctl", "show", HELPER_UNIT, "--property=MainPID", "--value"))

    def retry_completed(self) -> dict:
        return self.call({"operation": "create", "request_id": self.record["request_id"],
                          "email": self.record["email"],
                          "password": " new fixture password must not be written "})
