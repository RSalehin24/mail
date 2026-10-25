"""Actual non-root HTTPS runtime with real filesystem and Unix helper transport."""

import http.client
import json
import os
import ssl
import threading
import time
import uuid
from pathlib import Path

from backend.provisioning import InstallationPaths, create_installation
from helper.server import HelperServer, SocketPolicy
from host.docker import Docker
from host.image_tools import ImageTools
from shared.runtime_policy import RUNTIME_POLICY_NAME
from tests.helper_support import HelperFixture
from tests.tls_support import CertificateFixture
from tools.resources import WEB_MEMORY_MIB

STARTUP_TIMEOUT_SECONDS = 15
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


class RuntimeFixture:
    def __init__(self, helper=None):
        self.host = HelperFixture()
        self.root = self.host.root
        self.directories = {name: self.root / name for name in ("data", "keys", "socket")}
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
            os.chown(path, 0, 10001)
            path.chmod(0o640)
        os.chown(paths.database, 10001, 10001)
        os.chown(self.directories["data"], 10001, 10001)
        self.certificates = CertificateFixture(self.root)
        self.tls = self.certificates.issue()
        os.chown(self.tls, 0, 10001)
        self.tls.chmod(0o750)
        for path in self.tls.iterdir():
            os.chown(path, 0, 10001)
            path.chmod(0o640)
        self.identifier = ""
        self.server = HelperServer(
            self.directories["socket"] / "control.sock",
            (helper if helper is not None else self.host.helper, SocketPolicy(10001, 10001, 5)),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = ""

    def provision_directories(self):
        for path in self.directories.values():
            path.mkdir(mode=0o750)
            os.chown(path, 0, 10001)
            path.chmod(0o750)

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
        return tuple((self.mount_root / source, target) for source, target in (
            ("data", "/app/data"),
            ("keys", "/run/secrets/mail-admin"),
            ("installation.json", "/run/mail-admin-installation.json"),
            ("socket", "/run/mail-admin-helper"),
        ))

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
            "-p",
            "127.0.0.1::8443",
        )
        for source, target in self.bind_sources():
            readonly = "" if target == "/app/data" else ",readonly"
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
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.host.close()
