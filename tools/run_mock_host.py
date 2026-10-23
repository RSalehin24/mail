"""Operate a rebootable mock VM on the authorized host without host devices or Docker access."""

import argparse
import gzip
import hashlib
import json
import re
import shlex
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.run_remote import command, live_observation, ssh  # noqa: E402
from tools.vm.mock_profile import (  # noqa: E402
    BASE_SHA512,
    BASE_URL,
    CONTAINER_MEMORY_MIB,
    GUEST_MEMORY_MIB,
    HOST_RESERVE_MIB,
    MOCK_DOCKER_STARTUP_SECONDS,
)

DOCKER_UNIT = f"""[Unit]
Description=Isolated fixture Docker daemon
After=network-online.target containerd.service
Wants=network-online.target
Requires=containerd.service
[Service]
Type=notify
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/local/bin/dockerd --host=unix:///var/run/docker.sock \\
 --containerd=/run/containerd/containerd.sock --storage-driver=overlay2 \\
 --log-driver=local --log-opt max-size=5m --log-opt max-file=2
Restart=on-failure
RestartSec=2
TimeoutStartSec={MOCK_DOCKER_STARTUP_SECONDS}
[Install]
WantedBy=multi-user.target
"""
CONTAINERD_UNIT = """[Unit]
Description=Isolated fixture containerd
[Service]
Type=notify
ExecStart=/usr/local/bin/containerd --address /run/containerd/containerd.sock \\
 --state /run/containerd --root /var/lib/containerd
TimeoutStartSec=600
Restart=no
[Install]
WantedBy=multi-user.target
"""
DOWNLOAD = """import hashlib,os,sys,urllib.request
from pathlib import Path
os.umask(0o077)
root,url,expected = Path(sys.argv[1]),sys.argv[2],sys.argv[3]
digest = hashlib.sha512()
with urllib.request.urlopen(url,timeout=30) as response, (root/'base.qcow2').open('xb') as writer:
 for chunk in iter(lambda:response.read(1024*1024),b''):
  if writer.tell()+len(chunk)>512*1024*1024:
   raise RuntimeError('Mock base image exceeds the bounded download size')
  writer.write(chunk)
  digest.update(chunk)
 writer.flush()
 os.fsync(writer.fileno())
if digest.hexdigest() != expected:
 raise RuntimeError('Mock VM base image digest mismatch')
print('Verified mock VM base image')
"""


def cloud_config(public_key: str, engine_sha256: str) -> dict[str, object]:
    setup = (
        "set -eu\n"
        "curl --fail --silent --show-error http://10.0.2.2:8000/docker-engine.tar.gz "
        "-o /var/tmp/docker-engine.tar.gz\n"
        f"printf '%s  %s\\n' '{engine_sha256}' /var/tmp/docker-engine.tar.gz | sha256sum -c -\n"
        "tar -xzf /var/tmp/docker-engine.tar.gz -C /usr/local/bin\n"
        "ln -s /usr/local/bin/docker /usr/bin/docker\n"
        "install -d -m 0755 /usr/local/libexec/docker/cli-plugins\n"
        "install -m 0755 /usr/local/bin/cli-plugins/docker-compose "
        "/usr/local/libexec/docker/cli-plugins/docker-compose\n"
        "docker compose version\n"
        "command -v iptables\n"
        "getent group docker >/dev/null || groupadd --system docker\n"
        "systemctl daemon-reload\nsystemctl enable --now docker.service\n"
        "docker version --format '{{.Server.Version}}'\n"
        "touch /var/lib/mail-admin-mock-ready\n"
    )
    return {
        "users": [
            {
                "name": "fixture",
                "groups": ["sudo"],
                "shell": "/bin/bash",
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "lock_passwd": True,
                "ssh_authorized_keys": [public_key],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "swap": {"filename": "/swapfile", "size": 268435456, "maxsize": 268435456},
        "write_files": [
            {"path": "/etc/systemd/system/docker.service", "content": DOCKER_UNIT},
            {"path": "/etc/systemd/system/containerd.service", "content": CONTAINERD_UNIT},
            {
                "path": "/etc/systemd/journald.conf.d/mock.conf",
                "content": "[Journal]\nSystemMaxUse=16M\nRuntimeMaxUse=8M\n",
            },
        ],
        "runcmd": [setup],
    }


class MockHost:
    def __init__(self, state: Path) -> None:
        self.path = state.resolve()
        self.record = json.loads(self.path.read_bytes())
        self.host, self.name = self.record["host"], self.record["container"]
        if re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", self.host) is None:
            raise ValueError("Explicit SSH user@host required")
        if re.fullmatch(r"mail-admin-mock-host-[0-9a-f-]{36}", self.name) is None:
            raise ValueError("Invalid mock container identity")

    def inspect(self) -> dict:
        record = json.loads(ssh(self.host, ("sudo", "-n", "docker", "inspect", self.name)))[0]
        if record["Config"]["Labels"].get("mail-admin.mock-host") != self.record["id"]:
            raise RuntimeError("Refusing a container outside this mock fixture")
        return dict(record)

    def guest(self, arguments: tuple[str, ...]) -> str:
        inspected = self.inspect()
        port = int(inspected["NetworkSettings"]["Ports"]["2222/tcp"][0]["HostPort"])
        proxy = shlex.join(
            (
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-W",
                f"127.0.0.1:{port}",
                self.host,
            )
        )
        return command(
            (
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=45",
                "-o",
                "ServerAliveInterval=15",
                "-o",
                "ServerAliveCountMax=3",
                "-o",
                "HostKeyAlgorithms=ssh-ed25519",
                "-o",
                "ControlMaster=auto",
                "-o",
                "ControlPersist=600",
                "-o",
                "ControlPath=control",
                "-o",
                "IdentitiesOnly=yes",
                "-o",
                "ProxyCommand=" + proxy,
                "-o",
                "HostKeyAlias=" + self.name,
                "-o",
                "UserKnownHostsFile=" + str(self.path.parent / "known_hosts"),
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-i",
                str(self.path.parent / "identity"),
                "fixture@127.0.0.1",
                shlex.join(arguments),
            ),
            self.path.parent,
        )

    def control(self, action: str) -> None:
        inspected = self.inspect()
        actions = {
            "start-existing": ("start", self.name),
            "power-cut": ("kill", "--signal", "KILL", self.name),
        }
        if action not in actions:
            raise ValueError("Unknown fixture-only control action")
        if action == "start-existing":
            if inspected["State"]["Running"]:
                raise RuntimeError("The recorded mock is already running")
            require_capacity(self.host)
            for name, expected in self.record["bootstrap_sha256"].items():
                path = self.record["remote_directory"] + "/" + name
                if ssh(self.host, ("sha256sum", path)).split()[0] != expected:
                    raise RuntimeError("Prepared mock bootstrap changed")
            ssh(
                self.host,
                (
                    "sudo",
                    "-n",
                    "docker",
                    "update",
                    "--cpus",
                    "1",
                    "--memory",
                    str(CONTAINER_MEMORY_MIB) + "m",
                    "--memory-swap",
                    str(CONTAINER_MEMORY_MIB) + "m",
                    self.name,
                ),
            )
            self.record.update({"cpu_limit": 1, "container_memory_mib": CONTAINER_MEMORY_MIB})
            self.path.write_text(json.dumps(self.record, indent=2) + "\n")
        ssh(self.host, ("sudo", "-n", "docker") + actions[action])
        self.verify_live()

    def verify_live(self) -> None:
        current = json.loads(ssh(self.host, ("sudo", "-n", "docker", "inspect", "maddy")))[0]
        if live_observation(current) != self.record["live_before"]:
            raise RuntimeError("Live Maddy observation changed during mock testing")


def export_image(destination: Path) -> str:
    image = json.loads(command(("docker", "image", "inspect", "mail-admin:mock-host")))[0]
    raw = destination.with_suffix("")
    command(
        (
            "docker",
            "image",
            "save",
            "--platform",
            "linux/amd64",
            "-o",
            str(raw),
            "mail-admin:mock-host",
        )
    )
    current = json.loads(command(("docker", "image", "inspect", "mail-admin:mock-host")))[0]
    if current["Id"] != image["Id"]:
        raise RuntimeError("Mock image changed during export")
    with raw.open("rb") as reader, gzip.open(destination, "xb", compresslevel=1) as writer:
        shutil.copyfileobj(reader, writer)
    with destination.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def require_capacity(host: str) -> None:
    script = (
        "import json,shutil\n"
        "available=next(int(x.split()[1])*1024 for x in open('/proc/meminfo') "
        "if x.startswith('MemAvailable:'))\n"
        "print(json.dumps({'available_memory_bytes':available,"
        "'disk_free_bytes':shutil.disk_usage('/').free}))\n"
        f"if available<{CONTAINER_MEMORY_MIB + HOST_RESERVE_MIB}*1024*1024: "
        "raise RuntimeError('Insufficient spare memory for mock VM')\n"
        "if shutil.disk_usage('/').free<12*1024**3: "
        "raise RuntimeError('Insufficient mock disk capacity')\n"
        "print('Mock capacity check passed')\n"
    )
    print(ssh(host, ("python3", "-c", script)), flush=True)


def create(host: str) -> Path:
    if re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", host) is None:
        raise ValueError("Explicit SSH user@host required")
    require_capacity(host)
    identifier = str(uuid.uuid4())
    local = ROOT / "build" / ("mail-admin-mock-host-" + identifier)
    local.mkdir(mode=0o700)
    command(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(local / "identity")))
    digest = command(
        (
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--entrypoint",
            "sha256sum",
            "mail-admin:mock-host",
            "/opt/payload/docker-engine.tar.gz",
        )
    ).split()[0]
    public_key = (local / "identity.pub").read_text().strip()
    (local / "user-data").write_text(
        "#cloud-config\n" + json.dumps(cloud_config(public_key, digest))
    )
    (local / "meta-data").write_text(
        json.dumps({"instance-id": identifier, "local-hostname": "mail-admin-mock"})
    )
    archive = local / "mock-host.tar.gz"
    print("Exporting immutable mock VM tool image", flush=True)
    archive_digest = export_image(archive)
    return start_remote(host, (local, identifier, archive_digest))


def start_remote(host: str, values: tuple[Path, str, str]) -> Path:
    local, identifier, archive_digest = values
    home = ssh(host, ("python3", "-c", "from pathlib import Path; print(Path.home())"))
    if re.fullmatch(r"/home/[a-zA-Z0-9_-]+", home) is None:
        raise RuntimeError("Unexpected remote home")
    remote = ssh(host, ("mktemp", "-d", home + "/mail-admin-mock-host-" + identifier + "-XXXXXX"))
    if (
        re.fullmatch(
            re.escape(home + "/mail-admin-mock-host-" + identifier + "-") + r"[A-Za-z0-9]{6}",
            remote,
        )
        is None
    ):
        raise RuntimeError("Unexpected remote fixture directory")
    print("Transferring only mock VM artifacts", flush=True)
    for name in ("mock-host.tar.gz", "user-data", "meta-data"):
        command(
            (
                "scp",
                "-B",
                "-o",
                "StrictHostKeyChecking=yes",
                str(local / name),
                host + ":" + remote + "/" + name,
            )
        )
    if ssh(host, ("sha256sum", remote + "/mock-host.tar.gz")).split()[0] != archive_digest:
        raise RuntimeError("Mock tool archive digest mismatch")
    live = live_observation(json.loads(ssh(host, ("sudo", "-n", "docker", "inspect", "maddy")))[0])
    ssh(host, ("sudo", "-n", "docker", "load", "-i", remote + "/mock-host.tar.gz"))
    image = json.loads(
        ssh(host, ("sudo", "-n", "docker", "image", "inspect", "mail-admin:mock-host"))
    )[0]
    print("Downloading and verifying the isolated guest disk", flush=True)
    print(ssh(host, ("python3", "-c", DOWNLOAD, remote, BASE_URL, BASE_SHA512)), flush=True)
    state = local / "state.json"
    state.write_text(
        json.dumps(
            {
                "id": identifier,
                "host": host,
                "container": "mail-admin-mock-host-" + identifier,
                "remote_directory": remote,
                "base_url": BASE_URL,
                "base_sha512": BASE_SHA512,
                "archive_sha256": archive_digest,
                "image_id": image["Id"],
                "live_before": live,
            },
            indent=2,
        )
        + "\n"
    )
    launch_prepared(state)
    return state


def launch_prepared(state: Path) -> None:
    mock = MockHost(state)
    host, name, remote = mock.host, mock.name, mock.record["remote_directory"]
    mock.verify_live()
    if (
        re.fullmatch(r"/home/[a-zA-Z0-9_-]+/" + re.escape(name) + r"-[A-Za-z0-9]{6}", remote)
        is None
    ):
        raise RuntimeError("Refusing paths outside the prepared mock directory")
    require_capacity(host)
    bootstrap = stage_bootstrap(mock)
    mock.record.update(
        {
            "bootstrap_sha256": bootstrap,
            "launcher_sha256": bootstrap["start.py"],
            "guest_memory_mib": GUEST_MEMORY_MIB,
            "container_memory_mib": CONTAINER_MEMORY_MIB,
            "reserved_host_memory_mib": HOST_RESERVE_MIB,
        }
    )
    state.write_text(json.dumps(mock.record, indent=2) + "\n")
    owner = ssh(host, ("id", "-u")) + ":" + ssh(host, ("id", "-g"))
    identifier = mock.record["id"]
    ssh(
        host,
        (
            "sudo",
            "-n",
            "docker",
            "create",
            "--name",
            name,
            "--label",
            "mail-admin.fixture=true",
            "--label",
            "mail-admin.mock-host=" + identifier,
            "--user",
            owner,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            str(CONTAINER_MEMORY_MIB) + "m",
            "--memory-swap",
            str(CONTAINER_MEMORY_MIB) + "m",
            "--cpus",
            "1",
            "--pids-limit",
            "64",
            "--log-driver",
            "local",
            "--log-opt",
            "max-size=5m",
            "--log-opt",
            "max-file=2",
            "--tmpfs",
            "/tmp:size=8388608,mode=1777",  # noqa: S108 -- bounded private container tmpfs.
            "-p",
            "127.0.0.1::2222",
            "--mount",
            "type=bind,src=" + remote + ",dst=/fixture",
            "-e",
            "MOCK_GUEST_MEMORY_MIB=" + str(GUEST_MEMORY_MIB),
            "--entrypoint",
            "python",
            mock.record["image_id"],
            "/fixture/start.py",
        ),
    )
    ssh(host, ("sudo", "-n", "docker", "start", name))
    mock.verify_live()


def refresh_bootstrap(state: Path) -> None:
    mock = MockHost(state)
    if mock.inspect()["State"]["Running"]:
        raise RuntimeError("Stop only the mock VM before replacing its bootstrap")
    remote = mock.record["remote_directory"]
    expected = "/home/[a-zA-Z0-9_-]+/" + re.escape(mock.name) + r"-[A-Za-z0-9]{6}"
    if re.fullmatch(expected, remote) is None:
        raise RuntimeError("Invalid prepared mock directory")
    bootstrap = stage_bootstrap(mock)
    mock.record.update({"bootstrap_sha256": bootstrap, "launcher_sha256": bootstrap["start.py"]})
    state.write_text(json.dumps(mock.record, indent=2) + "\n")


def stage_bootstrap(mock: MockHost) -> dict[str, str]:
    return {
        name: stage_file(mock, ROOT / "tools/vm" / name)
        for name in ("start.py", "prepare.py", "mock_profile.py", "repair.py")
    }


def stage_file(mock: MockHost, source: Path) -> str:
    destination = mock.record["remote_directory"] + "/" + source.name
    command(
        ("scp", "-B", "-o", "StrictHostKeyChecking=yes", str(source), mock.host + ":" + destination)
    )
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    if ssh(mock.host, ("sha256sum", destination)).split()[0] != expected:
        raise RuntimeError("Mock file integrity check failed")
    return expected


def repair_prepared(state: Path) -> None:
    mock = MockHost(state)
    if mock.inspect()["State"]["Running"]:
        raise RuntimeError("Never repair a running mock disk")
    require_capacity(mock.host)
    refresh_bootstrap(state)
    stage_file(mock, repair_payload(mock))
    run_repair(mock)
    mock.verify_live()


def repair_payload(mock: MockHost) -> Path:
    original = json.loads((mock.path.parent / "user-data").read_text().split("\n", 1)[1])
    old_unit = next(
        entry["content"]
        for entry in original["write_files"]
        if entry["path"] == "/etc/systemd/system/docker.service"
    )
    payload = mock.path.parent / "repair-units.json"
    with payload.open("x") as writer:
        json.dump(
            {
                "original_docker_sha256": hashlib.sha256(old_unit.encode()).hexdigest(),
                "units": {"docker.service": DOCKER_UNIT, "containerd.service": CONTAINERD_UNIT},
            },
            writer,
        )
    return payload


def run_repair(mock: MockHost) -> None:
    remote = mock.record["remote_directory"]
    owner = ssh(mock.host, ("id", "-u")) + ":" + ssh(mock.host, ("id", "-g"))
    print(
        ssh(
            mock.host,
            (
                "sudo", "-n", "docker", "run", "--rm", "--network", "none", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "--memory", "192m", "--memory-swap", "192m", "--cpus", "1",
                "--pids-limit", "64", "--label", "mail-admin.fixture=true", "--user", owner,
                "--mount", "type=bind,src=" + remote + ",dst=/fixture",
                "--entrypoint", "python", mock.record["image_id"], "/fixture/repair.py",
            ),
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "create",
            "launch-prepared",
            "refresh-bootstrap",
            "repair-prepared",
            "guest",
            "start-existing",
            "power-cut",
            "status",
        ),
    )
    parser.add_argument(
        "reference", help="Authorized SSH host for create; otherwise the local state JSON"
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.action == "create":
        print(json.dumps({"state": str(create(arguments.reference)), "guest": "booting"}))
        return
    mock = MockHost(Path(arguments.reference))
    if arguments.action == "launch-prepared":
        launch_prepared(Path(arguments.reference))
    elif arguments.action == "refresh-bootstrap":
        refresh_bootstrap(Path(arguments.reference))
    elif arguments.action == "repair-prepared":
        repair_prepared(Path(arguments.reference))
    elif arguments.action == "guest":
        print(mock.guest(tuple(arguments.arguments)))
    elif arguments.action == "status":
        print(json.dumps({"container_state": mock.inspect()["State"]["Status"]}))
        mock.verify_live()
    else:
        mock.control(arguments.action)


if __name__ == "__main__":
    main()
