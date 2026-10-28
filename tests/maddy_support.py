"""Real, local-only Maddy fixture. Refuses remote Docker endpoints and existing containers."""

import imaplib
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from helper.journal import Journal
from helper.maddy import DockerMaddy
from helper.service import HelperDependencies, HostHelper
from host.locks import LockPolicy
from tests.helper_support import DeletePolicy
from tests.tls_support import CertificateFixture
from tools.maddy_fixture import fixture_reference
from tools.resources import MADDY_MEMORY_MIB, MADDY_RUNTIME_TMPFS_BYTES

CONFIGURATION = """hostname mail.rsalehin24.me
state_dir /data
runtime_dir /run/maddy
tls file /data/tls/fullchain.pem /data/tls/privkey.pem
auth.pass_table local_authdb {
 table sql_table {
  driver sqlite3
  dsn credentials.db
  table_name passwords
 }
}
storage.imapsql local_mailboxes {
 driver sqlite3
 dsn imapsql.db
}
smtp tcp://0.0.0.0:25 {
 default_source {
  destination rsalehin24.me {
   deliver_to &local_mailboxes
  }
  default_destination {
   reject 550 5.1.1 "Fixture recipient does not exist"
  }
 }
}
imap tls://0.0.0.0:993 {
 auth &local_authdb
 storage &local_mailboxes
}
"""


def imap_login(fixture, email, password):
    context = ssl.create_default_context(cafile=fixture.certificates.trust)
    connection = imaplib.IMAP4_SSL("127.0.0.1", fixture.imap_port, ssl_context=context, timeout=5)
    try:
        credentials = ("\0" + email + "\0" + password).encode("utf-8")
        connection.authenticate("PLAIN", lambda _: credentials)
    except BaseException:
        connection.shutdown()
        raise
    return connection


class FixtureMaddy(DockerMaddy):
    def expected_cli_version(self):
        return '0.9.5+postmaster-fixture.1'


class RealMaddyFixture:
    maddy_class = FixtureMaddy

    def image_reference(self):
        return fixture_reference()

    def __init__(self, configuration=CONFIGURATION):
        self.docker = shutil.which("docker")
        if not self.docker:
            raise RuntimeError("Docker is required; cases cannot be skipped")
        self.container = "mail-admin-fixture-" + str(uuid.uuid4())
        self._local_engine()
        existing = self.command(
            ("ps", "-a", "--filter", "name=^/" + self.container + "$", "--format", "{{.ID}}")
        )
        if existing.stdout.strip():
            raise RuntimeError("Refusing to touch an existing maddy container")
        workspace = Path(
            os.environ.get(
                "MAIL_ADMIN_FIXTURE_ROOT", str(Path(__file__).resolve().parents[1] / "build")
            )
        )
        workspace.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="mail-admin-isolated-", dir=workspace)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        self.data.chmod(0o700)
        (self.data / "maddy.conf").touch(mode=0o600)
        (self.data / "maddy.conf").chmod(0o600)
        (self.data / "tls").mkdir(mode=0o700)
        (self.data / "tls").chmod(0o700)
        self.certificates = CertificateFixture(self.root)
        self.tls = self.certificates.issue()
        self.configuration = self.root / "maddy.conf"
        self.configuration.write_text(configuration)
        self.configuration.chmod(0o600)
        self.identifier = ""

    def command(self, arguments, payload=None):
        arguments = tuple(self.container if value == "maddy" else value for value in arguments)
        return subprocess.run(
            (self.docker,) + tuple(arguments),
            input=payload,
            capture_output=True,
            text=not isinstance(payload, bytes),
            timeout=30,
            check=True,
        )

    def _local_engine(self):
        context = self.command(("context", "inspect"))
        endpoint = json.loads(context.stdout)[0]["Endpoints"]["docker"]["Host"]
        override = os.environ.get("DOCKER_HOST", endpoint)
        if not endpoint.startswith("unix://") or not override.startswith("unix://"):
            raise RuntimeError("Qualification requires a local Unix Docker endpoint")

    def launch(self):
        launcher = self.root / "maddy-launcher"
        shutil.copy2(Path(__file__).resolve().parents[1] / "host/maddy-launcher", launcher)
        launcher.chmod(0o755)
        mounts = (
            "--mount",
            f"type=bind,src={self.daemon_path(self.data)},dst=/data",
            "--mount",
            f"type=bind,src={self.daemon_path(self.configuration)},dst=/data/maddy.conf,readonly",
            "--mount",
            f"type=bind,src={self.daemon_path(self.tls)},dst=/data/tls,readonly",
            "--mount",
            f"type=bind,src={self.daemon_path(launcher)},dst=/usr/local/libexec/mail-admin-maddy,readonly",
        )
        arguments = (
            "create",
            "--platform",
            "linux/amd64",
            "--name",
            self.container,
            "--label",
            "mail-admin.fixture=" + str(uuid.uuid4()),
            "--memory",
            str(MADDY_MEMORY_MIB) + "m",
            "--memory-swap",
            str(MADDY_MEMORY_MIB) + "m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "128",
            "--tmpfs",
            f"/run/maddy:size={MADDY_RUNTIME_TMPFS_BYTES},mode=0700",
            "-p",
            "127.0.0.1::993",
            "-p",
            "127.0.0.1::25",
        )
        result = self.command(
            arguments
            + mounts
            + (
                "--entrypoint",
                "/usr/local/libexec/mail-admin-maddy",
                self.image_reference(),
                "-config",
                "/data/maddy.conf",
                "run",
            )
        )
        self.identifier = result.stdout.strip()
        self.command(("start", self.identifier))
        inspected = json.loads(self.command(("inspect", self.identifier)).stdout)[0]
        self.refresh_ports(inspected)
        self._wait()
        self.maddy_image = inspected['Image']
        return inspected

    def start(self):
        inspected = self.launch()
        image = inspected["Image"]
        mounts = {mount["Destination"]: mount["Source"] for mount in inspected["Mounts"]}
        self.maddy = self.maddy_class(
            {
                "inputs": {"maddy_image_id": image, "operation_timeout_seconds": 10},
                "maddy_mounts": mounts,
                "maddy_data_directory": str(self.data),
            },
            self.helper_docker(),
        )
        for arguments in (("creds", "list"), ("imap-acct", "list")):
            response = self.maddy.change(arguments)
            if response.code:
                raise RuntimeError("Fixture database provisioning failed")
        journal = Journal.provision(self.root / "journal.db")
        lock = self.root / "maddy.lock"
        lock.touch(mode=0o600)
        self.deletion = DeletePolicy()
        self.helper = HostHelper(
            HelperDependencies(
                self.maddy,
                journal,
                self.deletion,
                lock,
                LockPolicy(1, os.getuid()),
                time.time,
                lambda: {},
            )
        )

    def helper_docker(self, observation=None):
        container, image = observation or (self.container, self.maddy_image)
        if (not container.startswith('mail-admin-fixture-')
                or str(uuid.UUID(container.removeprefix('mail-admin-fixture-')))
                != container.removeprefix('mail-admin-fixture-')
                or re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None):
            raise RuntimeError('Only exact private fixture identities may be observed')
        images = (self.maddy_image, image)
        wrapper = self.root / "fixture-docker"
        source = (
            "#!/usr/local/bin/python\n"
            "import os,sys\n"
            "arguments=sys.argv[1:]\n"
            "if arguments[:2]==['inspect','maddy'] and len(arguments)==2:\n"
            f" arguments[1]={container!r}\n"
            f"elif arguments[:2]==['image','inspect'] and len(arguments)==3 "
            f"and arguments[2] in {images!r}:\n"
            " pass\n"
            "elif arguments[:4]==['exec','-i','maddy','/usr/local/libexec/mail-admin-maddy']:\n"
            f" arguments[2]={container!r}\n"
            "else:\n"
            " raise SystemExit('Refusing non-fixture Docker command')\n"
            f"os.execv({self.docker!r},[{self.docker!r}]+arguments)\n"
        )
        wrapper.write_text(source)
        wrapper.chmod(0o700)
        return str(wrapper)

    def daemon_path(self, path):
        source = os.environ.get("MAIL_ADMIN_FIXTURE_DAEMON_ROOT")
        root = os.environ.get("MAIL_ADMIN_FIXTURE_ROOT")
        return str(Path(source) / path.relative_to(root)) if source and root else str(path)

    def refresh_ports(self, inspected=None):
        if inspected is None:
            inspected = json.loads(self.command(("inspect", self.identifier)).stdout)[0]
        ports = inspected["NetworkSettings"]["Ports"]
        self.imap_port = int(ports["993/tcp"][0]["HostPort"])
        self.smtp_port = int(ports["25/tcp"][0]["HostPort"])

    def _wait(self):
        deadline = time.monotonic() + 15
        context = ssl.create_default_context(cafile=self.certificates.trust)
        while time.monotonic() < deadline:
            try:
                with imaplib.IMAP4_SSL(
                    "127.0.0.1", self.imap_port, ssl_context=context, timeout=0.5
                ) as connection:
                    if connection.noop()[0] == "OK":
                        return
            except (OSError, imaplib.IMAP4.error):
                time.sleep(0.1)
        response = self.command(("logs", self.identifier))
        raise RuntimeError(
            "Fixture Maddy did not start: " + (response.stdout + response.stderr)[-4000:]
        )

    def close(self):
        try:
            if self.identifier:
                self._remove()
        finally:
            self.temporary.cleanup()

    def _remove(self):
        try:
            record = json.loads(self.command(("inspect", self.identifier)).stdout)[0]
            peak = None
            if record["State"]["Running"]:
                peak = int(
                    self.command(
                        ("exec", self.identifier, "/bin/cat", "/sys/fs/cgroup/memory.peak")
                    ).stdout
                )
            observation = {
                "scope": "isolated Maddy container; not production-host peak",
                "image_id": record["Image"],
                "memory_peak_bytes": peak,
                "memory_limit_bytes": record["HostConfig"]["Memory"],
                "oom_killed": record["State"]["OOMKilled"],
            }
            path = Path("build") / (self.container + "-memory.json")
            path.write_text(json.dumps(observation, indent=2) + "\n")
        finally:
            self.command(("rm", "--force", self.identifier))
