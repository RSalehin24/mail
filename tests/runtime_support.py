"""Actual non-root HTTPS runtime: the dashboard runs the host operations in-process."""

import http.client
import json
import os
import ssl
import time
import uuid
from pathlib import Path

from backend.provisioning import InstallationPaths, create_installation
from helper.journal import Journal
from host.docker import Docker
from host.image_tools import ImageTools
from shared.runtime_policy import RUNTIME_POLICY_NAME
from tests.maddy_support import SERVICE_ID, maddy_runtime_tmpfs
from tests.support import StateFixture
from tests.tls_support import CertificateFixture
from tools.resources import WEB_MEMORY_MIB

STARTUP_TIMEOUT_SECONDS = 15
# An authenticated read that needs no Maddy, for fixtures that run without a mail server.
AUTHENTICATED_PROBE = "/api/session"
OPERATION_TIMEOUT_SECONDS = 5
STATE_TARGET = "/var/lib/mail-admin-helper"
LOCK_TARGET = "/run/lock/mail-admin-maddy.lock"
# Directories root tools write into and replace files in atomically; the container mounts them
# read-only. Each maps a fixture directory name to its path in the container.
OBSERVATION_DIRECTORIES = {
    "etc": "/run/mail-admin-host/etc",
    "backup": "/var/lib/mail-admin-backup",
    "alerts": "/var/lib/mail-admin-alerts",
}
RECORD_DIRECTORIES = {"deployment.json": "etc", "helper-policy.json": "etc",
                      "manifest.json": "backup"}
RESOURCE_OBSERVATION = """import json
from pathlib import Path
fields = {'Name', 'State', 'Threads', 'VmRSS', 'VmHWM'}
status = {
 key:value.strip() for line in Path('/proc/1/status').read_text().splitlines()
 for key,separator,value in [line.partition(':')] if separator and key in fields
}
print(json.dumps({
 'process':status,
 'wait_channel':Path('/proc/1/wchan').read_text().strip(),
 'cgroup':{name:Path('/sys/fs/cgroup',name).read_text().strip()
           for name in ('cpu.stat','memory.current','memory.events')}
}))
"""


class MaddyFiles:
    """The paths the dashboard mounts from Maddy; a started Maddy fixture supplies real ones."""

    def __init__(self, data, configuration, tls):
        self.data, self.configuration, self.tls = data, configuration, tls

    @classmethod
    def empty(cls, root):
        """Placeholders for tests that never touch a mailbox; no Maddy server runs."""
        files = cls(root / "maddy-data", root / "maddy.conf", root / "maddy-tls")
        for directory in (files.data, files.tls):
            directory.mkdir(mode=0o700)
            os.chown(directory, SERVICE_ID, SERVICE_ID)
        files.configuration.touch(mode=0o600)
        os.chown(files.configuration, SERVICE_ID, SERVICE_ID)
        return files


class RuntimeFixture:
    writable_targets = frozenset({"/app/data", "/data", STATE_TARGET, LOCK_TARGET})

    def __init__(self, maddy=None, deployment_inputs=None):
        self.state_fixture = StateFixture()
        self.root = self.state_fixture.root
        self.maddy = maddy
        self.maddy_files = (
            MaddyFiles(maddy.data, maddy.configuration, maddy.tls)
            if maddy is not None
            else MaddyFiles.empty(self.root)
        )
        self.directories = {name: self.root / name for name in ("data", "keys")}
        self.provision_directories()
        paths = InstallationPaths(
            self.directories["data"] / "admin_data.db",
            self.directories["keys"],
            self.root / "installation.json",
        )
        self.database, self.keys = create_installation(paths)
        for path in (
            paths.marker,
            paths.secrets / "encryption.key",
            paths.secrets / "verifier.key",
            paths.secrets / RUNTIME_POLICY_NAME,
        ):
            os.chown(path, 0, SERVICE_ID)
            path.chmod(0o640)
        os.chown(paths.database, SERVICE_ID, SERVICE_ID)
        os.chown(self.directories["data"], SERVICE_ID, SERVICE_ID)
        self.certificates = CertificateFixture(self.root)
        self.tls = self.certificates.issue()
        os.chown(self.tls, 0, SERVICE_ID)
        self.tls.chmod(0o750)
        for path in self.tls.iterdir():
            os.chown(path, 0, SERVICE_ID)
            path.chmod(0o640)
        self.identifier = ""
        self.cookie = ""
        self.provision_host(deployment_inputs or {})

    def provision_directories(self):
        for path in self.directories.values():
            path.mkdir(mode=0o750)
            os.chown(path, 0, SERVICE_ID)
            path.chmod(0o750)

    def provision_host(self, deployment_inputs):
        """State, lock and records the in-process host expects, owned as production owns them."""
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.lock = self.root / "maddy.lock"
        self.lock.touch(mode=0o600)
        for path in (self.state, self.lock):
            os.chown(path, SERVICE_ID, SERVICE_ID)
        self.deployment = {
            "inputs": {
                "lock_wait_seconds": OPERATION_TIMEOUT_SECONDS,
                "operation_timeout_seconds": OPERATION_TIMEOUT_SECONDS,
                **deployment_inputs,
            },
            "backup_sources": {},
            "maddy_cli_version": "0.9.6",
            "state_owner_uid": SERVICE_ID,
        }
        self.observation_directories = {}
        for name in OBSERVATION_DIRECTORIES:
            directory = self.root / name
            directory.mkdir(mode=0o750)
            self.grant_dashboard(directory)
            self.observation_directories[name] = directory
        self.records = {name: self.observation_directories[directory] / name
                        for name, directory in RECORD_DIRECTORIES.items()}
        for name, path in self.records.items():
            path.touch(mode=0o640)
            self.grant_dashboard(path)
            self.write_record(name, self.deployment if name == "deployment.json" else {})

    @staticmethod
    def grant_dashboard(path):
        """What the cutover does once: the file or directory stays root's, readable by the group."""
        os.chown(path, 0, SERVICE_ID)
        path.chmod(0o750 if path.is_dir() else 0o640)

    def write_record(self, name, content):
        """Rewrite in place: the container binds the file, so its inode must not change."""
        self.records[name].write_text(json.dumps(content))

    def journal(self):
        return Journal(self.state / "state.db", SERVICE_ID)

    def command(self, arguments):
        import subprocess

        return subprocess.run(
            ("docker",) + tuple(arguments), capture_output=True, text=True, timeout=30, check=True
        )

    def daemon_path(self, path):
        root, source = (
            os.environ["MAIL_ADMIN_FIXTURE_ROOT"],
            os.environ["MAIL_ADMIN_FIXTURE_DAEMON_ROOT"],
        )
        return str(Path(source) / path.relative_to(root))

    def start(self):
        self.mount_root = self.root
        self._start_container()

    def bind_sources(self):
        application = tuple((self.mount_root / source, target) for source, target in (
            ("data", "/app/data"),
            ("keys", "/run/secrets/mail-admin"),
            ("installation.json", "/run/mail-admin-installation.json"),
        ))
        return application + self.host_sources()

    def host_sources(self):
        files = self.maddy_files
        return (
            (files.data, "/data"),
            (files.configuration, "/data/maddy.conf"),
            (files.tls, "/data/tls"),
            (self.state, STATE_TARGET),
            (self.lock, LOCK_TARGET),
        ) + tuple((self.observation_directories[name], target)
                  for name, target in OBSERVATION_DIRECTORIES.items())

    def _start_container(self):
        self.launch()
        self.ready()

    def launch(self):
        command = (
            "create",
            "--name",
            "mail-admin-runtime-" + str(uuid.uuid4()),
            "--label",
            "mail-admin.fixture=true",
            "--platform",
            "linux/amd64",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            str(WEB_MEMORY_MIB) + "m",
            "--memory-swap",
            str(WEB_MEMORY_MIB) + "m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:size=8388608,mode=1777",  # noqa: S108 -- private bounded container tmpfs.
            "--tmpfs",
            maddy_runtime_tmpfs(),
            "-p",
            "127.0.0.1::8443",
        )
        for source, target in self.bind_sources():
            readonly = "" if target in self.writable_targets else ",readonly"
            command += (
                "--mount",
                "type=bind,src="
                + self.daemon_path(source)
                + f",dst={target}{readonly}",
            )
        command += ("--mount", "type=bind,src=" + self.daemon_path(self.tls) + ",dst=/tls,readonly")
        for value in (
            "HASH_MEMORY_KIB=8192",
            "HASH_PARALLELISM=1",
            "HTTP_CONCURRENCY=4",
            "OPERATION_TIMEOUT_SECONDS=5",
        ):
            command += ("-e", value)
        command += (os.environ["MAIL_ADMIN_RUNTIME_REFERENCE"],)
        self.identifier = self.command(command).stdout.strip()
        self.command(("start", self.identifier))

    def ready(self):
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        last_observation = "not observed"
        while time.monotonic() < deadline:
            inspected = json.loads(self.command(("inspect", self.identifier)).stdout)[0]
            if not inspected["State"]["Running"]:
                last_observation = "container stopped"
                break
            mappings = (inspected["NetworkSettings"]["Ports"] or {}).get("8443/tcp")
            if not mappings:
                last_observation = "port not published"
                time.sleep(0.25)
                continue
            self.port = int(mappings[0]["HostPort"])
            try:
                status = self.api("GET", "/api/status")[0]
                if status == 200:
                    return
                last_observation = f"HTTP {status}"
            except OSError as error:
                last_observation = type(error).__name__
            time.sleep(0.25)
        raise RuntimeError(
            f"Runtime did not become healthy ({last_observation}): " + self.diagnostics()
        )

    def diagnostics(self):
        state = json.loads(self.command(("inspect", self.identifier)).stdout)[0]["State"]
        observation = {
            key: state[key] for key in ("Status", "Running", "ExitCode", "OOMKilled", "Error")
        }
        logs = self.command(("logs", "--tail", "12", self.identifier))
        observation["startup_log"] = logs.stdout + logs.stderr
        if state["Running"]:
            observation["resources"] = json.loads(
                self.command(("exec", self.identifier, "python", "-c", RESOURCE_OBSERVATION)).stdout
            )
        return json.dumps(observation)

    def exited(self):
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            state = json.loads(self.command(("inspect", self.identifier)).stdout)[0]["State"]
            if not state["Running"] and not state["Restarting"]:
                return state
            time.sleep(0.25)
        raise RuntimeError("Runtime failed to stop: " + self.diagnostics())

    def operator_deployment(self, identifier=None):
        container = json.loads(self.command(("inspect", identifier or self.identifier)).stdout)[0]
        names = {
            "HASH_MEMORY_KIB",
            "HASH_PARALLELISM",
            "HTTP_CONCURRENCY",
            "OPERATION_TIMEOUT_SECONDS",
        }
        environment = {
            key: value
            for entry in container["Config"]["Env"]
            for key, value in [entry.split("=", 1)]
            if key in names
        }
        deployment = {
            "dashboard_image_id": os.environ["MAIL_ADMIN_RUNTIME_REFERENCE"],
            "inputs": {"service_uid": 10001, "service_gid": 10001},
            "consumers": {"mail-admin": {"environment": environment}},
            "transient_resources": {
                "memory_limit_bytes": WEB_MEMORY_MIB * 1024 * 1024,
                "tmpfs_limit_bytes": 8 * 1024 * 1024,
                "cpu_limit": 0.5,
            },
        }
        return deployment

    def probe_arguments(self):
        deployment = self.operator_deployment()
        arguments = ImageTools(Docker("docker"), deployment).authentication_arguments("probe")
        paths = {
            "/opt/mail-admin/data": self.root / "data",
            "/etc/mail-admin/secrets": self.root / "keys",
            "/etc/mail-admin/installation.json": self.root / "installation.json",
        }
        rewritten = []
        for argument in arguments:
            if argument.startswith("type=bind,src="):
                source = argument.split(",")[1].removeprefix("src=")
                argument = argument.replace(
                    "src=" + source + ",", "src=" + self.daemon_path(paths[source]) + ",", 1
                )
            rewritten.append(argument)
        return tuple(rewritten)

    def api(self, method, path, body=None):
        context = ssl.create_default_context(cafile=self.certificates.trust)
        # Outlive the server's five-second work deadline so its safe 503 is observable.
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.port, context=context, timeout=10
        )
        headers = {
            "Host": "mail.rsalehin24.me",
            "Origin": "https://mail.rsalehin24.me",
            "Content-Type": "application/json",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            payload = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, payload, headers)
            response = connection.getresponse()
            content = response.read()
            result = (
                json.loads(content)
                if response.getheader("content-type", "").startswith("application/json")
                else content.decode()
            )
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def close(self):
        if self.identifier:
            self.command(("rm", "--force", self.identifier))
        self.state_fixture.close()
