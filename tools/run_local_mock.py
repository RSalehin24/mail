"""Operate a local software-emulated amd64 guest; no production connection is accepted."""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.run_linux import docker  # noqa: E402
from tools.run_mock_host import DOWNLOAD, cloud_config  # noqa: E402
from tools.run_remote import command  # noqa: E402
from tools.ssh_proxy import docker_ssh_proxy  # noqa: E402
from tools.vm.mock_profile import (  # noqa: E402
    BASE_SHA512,
    BASE_URL,
    HOST_RESERVE_MIB,
    LOCAL_CONTAINER_MEMORY_MIB,
    LOCAL_DISK_RESERVE_BYTES,
    LOCAL_GUEST_MEMORY_MIB,
)

MEMORY_OBSERVATION = """import json,shutil
available=next(int(x.split()[1])*1024 for x in open('/proc/meminfo')
 if x.startswith('MemAvailable:'))
print(json.dumps({'available_memory_bytes':available,
 'disk_free_bytes':shutil.disk_usage('/fixture').free}))
"""
SEED_WRITER = """import json,os,sys
from pathlib import Path
os.umask(0o077)
for name,content in json.load(sys.stdin).items():
 if name not in {'user-data','meta-data'}: raise RuntimeError('Invalid seed name')
 with (Path('/fixture')/name).open('x') as output: output.write(content)
"""


def native_architecture(value: str) -> str:
    aliases = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
    if value not in aliases:
        raise RuntimeError("The local mock requires a native ARM64 or amd64 Linux engine")
    return aliases[value]


def local_engine() -> dict:
    context = json.loads(docker(("context", "inspect")))[0]
    endpoint = os.environ.get("DOCKER_HOST", context["Endpoints"]["docker"]["Host"])
    if not endpoint.startswith("unix://") or not context["Endpoints"]["docker"]["Host"].startswith(
        "unix://"
    ):
        raise RuntimeError("A local Unix Docker endpoint is required")
    engine = json.loads(docker(("info", "--format", "{{json .}}")))
    if engine["OSType"] != "linux":
        raise RuntimeError("The local mock requires a Linux engine")
    native_architecture(engine["Architecture"])
    return dict(engine)


def utility(image: str, volume: str, architecture: str = "arm64") -> tuple[str, ...]:
    return (
        "run", "--rm", "--platform", "linux/" + native_architecture(architecture),
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--memory", "128m", "--memory-swap",
        "128m", "--pids-limit", "32", "--cpus", "1", "--label", "mail-admin.fixture=true",
        "--mount", f"type=volume,src={volume},dst=/fixture", "--entrypoint", "python", image,
    )


def require_capacity(
    image: str, volume: str, architecture: str = "arm64",
) -> dict[str, int]:
    host_free = shutil.disk_usage(ROOT).free
    if host_free < LOCAL_DISK_RESERVE_BYTES:
        raise RuntimeError("The local mock needs at least 3 GiB of free workstation disk space")
    observation = json.loads(docker(
        utility(image, volume, architecture) + ("-c", MEMORY_OBSERVATION)
    ))
    required = (LOCAL_CONTAINER_MEMORY_MIB + HOST_RESERVE_MIB) * 1024 * 1024
    if observation["available_memory_bytes"] < required:
        raise RuntimeError("Insufficient spare Linux engine memory for the local mock")
    if observation["disk_free_bytes"] < LOCAL_DISK_RESERVE_BYTES:
        raise RuntimeError("Insufficient Linux engine disk space for the local mock")
    return {**observation, "workstation_disk_free_bytes": host_free}


def create(image_reference: str, acceleration: str = "tcg") -> Path:
    os.umask(0o077)
    engine = local_engine()
    architecture = native_architecture(engine["Architecture"])
    if acceleration not in {"tcg", "kvm"}:
        raise ValueError("Unknown guest acceleration")
    if acceleration == "kvm" and (architecture != "amd64"
            or not os.access("/dev/kvm", os.R_OK | os.W_OK)):
        raise RuntimeError("KVM requires an accessible native amd64 virtualization device")
    image = json.loads(docker(("image", "inspect", image_reference)))[0]
    if (image["Os"], image["Architecture"], image["Config"]["User"]) != (
        "linux", architecture, "10001:10001"
    ):
        raise RuntimeError("A native unprivileged local mock tool image is required")
    identifier = str(uuid.uuid4())
    name = "mail-admin-local-mock-" + identifier
    volume = name + "-disk"
    if shutil.disk_usage(ROOT).free < LOCAL_DISK_RESERVE_BYTES:
        raise RuntimeError("The local mock needs at least 3 GiB of free workstation disk space")
    docker(("volume", "create", "--label", "mail-admin.fixture=true", volume))
    capacity = require_capacity(image["Id"], volume, architecture)
    local = ROOT / "build" / name
    local.mkdir(mode=0o700)
    command(("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(local / "identity")))
    seed(image["Id"], (volume, local, identifier), architecture)
    state = local / "state.json"
    record = {
        "id": identifier, "container": name, "volume": volume, "image_id": image["Id"],
        "engine_id": engine["ID"], "engine_architecture": engine["Architecture"],
        "guest_architecture": "amd64",
        "acceleration": "kvm" if acceleration == "kvm" else "software-emulation",
        "scope": "disposable mock setup; not application or native-host qualification",
        "base_url": BASE_URL, "base_sha512": BASE_SHA512, "capacity_before": capacity,
        "created_at": datetime.now(UTC).isoformat(),
        "guest_memory_mib": LOCAL_GUEST_MEMORY_MIB,
        "container_memory_mib": LOCAL_CONTAINER_MEMORY_MIB,
        "reserved_engine_memory_mib": HOST_RESERVE_MIB,
    }
    state.write_text(json.dumps(record, indent=2) + "\n")
    print("Downloading the checksum-pinned public mock disk", flush=True)
    docker(utility(image["Id"], volume, architecture) + (
        "-c", DOWNLOAD, "/fixture", BASE_URL, BASE_SHA512,
    ))
    docker(launch_arguments(record))
    inspection = json.loads(docker(("inspect", name)))[0]
    record["container_id"] = inspection["Id"]
    state.write_text(json.dumps(record, indent=2) + "\n")
    LocalMock(state).control("start-existing")
    return state


def seed(
    image: str, values: tuple[str, Path, str], architecture: str = "arm64",
) -> None:
    volume, local, identifier = values
    digest = docker(utility(image, volume, architecture) + (
        "-c", "import hashlib; print(hashlib.file_digest(open("
        "'/opt/payload/docker-engine.tar.gz','rb'),'sha256').hexdigest())"
    )).strip()
    configuration = cloud_config((local / "identity.pub").read_text().strip(), digest)
    contents = {
        "user-data": "#cloud-config\n" + json.dumps(configuration),
        "meta-data": json.dumps({"instance-id": identifier, "local-hostname": "mail-admin-mock"}),
    }
    arguments = ("docker",) + utility(image, volume, architecture)
    arguments = arguments[:2] + ("--interactive",) + arguments[2:]
    subprocess.run(  # noqa: S603 -- public fixture seed only, written to a new private volume.
        arguments + ("-c", SEED_WRITER), input=json.dumps(contents), text=True, check=True,
        capture_output=True, timeout=60
    )


def launch_arguments(record: dict) -> tuple[str, ...]:
    memory = str(LOCAL_CONTAINER_MEMORY_MIB) + "m"
    accelerated = record.get("acceleration") == "kvm"
    device = ("--device", "/dev/kvm:/dev/kvm:rw", "-e", "MOCK_ACCELERATION=kvm")
    return (
        "create", "--name", record["container"], "--label", "mail-admin.fixture=true",
        "--label", "mail-admin.local-mock=" + record["id"], "--platform",
        "linux/" + native_architecture(record.get("engine_architecture", "arm64")),
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--memory", memory, "--memory-swap", memory, "--cpus", "1", "--pids-limit", "64",
        "--log-driver", "local", "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
        "--tmpfs", "/tmp:size=8388608,mode=1777",  # noqa: S108 -- bounded private tmpfs.
        "-p", "127.0.0.1::2222", "--mount",
        "type=volume,src=" + record["volume"] + ",dst=/fixture",
        "-e", "MOCK_GUEST_MEMORY_MIB=" + str(LOCAL_GUEST_MEMORY_MIB),
        "-e", "MOCK_LOCAL_REPLACEMENT=1",
    ) + (device if accelerated else ()) + (record["image_id"],)


class LocalMock:
    def __init__(self, state: Path) -> None:
        self.path = state.resolve()
        self.record = json.loads(self.path.read_bytes())
        self.name = self.record["container"]
        if self.name != "mail-admin-local-mock-" + str(uuid.UUID(self.record["id"])):
            raise ValueError("Invalid local mock identity")
        if self.record["volume"] != self.name + "-disk":
            raise ValueError("Invalid local mock volume")

    def inspect(self) -> dict:
        engine = local_engine()
        if (engine["ID"] != self.record["engine_id"]
                or native_architecture(engine.get("Architecture", "arm64"))
                != native_architecture(self.record.get("engine_architecture", "arm64"))):
            raise RuntimeError("The recorded local engine changed")
        inspected = json.loads(docker(("inspect", self.name)))[0]
        expected = self.record
        devices = ([{"PathOnHost": "/dev/kvm", "PathInContainer": "/dev/kvm",
                     "CgroupPermissions": "rw"}] if expected.get("acceleration") == "kvm"
                   else [])
        if (
            inspected["Id"] != expected["container_id"]
            or inspected["Image"] != expected["image_id"]
            or inspected["Config"]["Labels"].get("mail-admin.local-mock") != expected["id"]
            or inspected["HostConfig"]["Privileged"]
            or inspected["HostConfig"].get("Devices", []) != devices
            or inspected["HostConfig"]["Memory"] != LOCAL_CONTAINER_MEMORY_MIB * 1024 * 1024
            or inspected["HostConfig"]["MemorySwap"] != LOCAL_CONTAINER_MEMORY_MIB * 1024 * 1024
            or inspected["HostConfig"]["NanoCpus"] != 1_000_000_000
            or inspected["HostConfig"]["PidsLimit"] != 64
            or not inspected["HostConfig"]["ReadonlyRootfs"]
            or [(m["Type"], m.get("Name"), m["Destination"]) for m in inspected["Mounts"]
                if m["Type"] != "tmpfs"]
            != [("volume", expected["volume"], "/fixture")]
        ):
            raise RuntimeError("The exact owned local mock container is required")
        return dict(inspected)

    def control(self, action: str, capacity_image: str | None = None) -> None:
        if capacity_image is not None and action != "start-existing":
            raise ValueError("A capacity image is only used when restarting a stopped mock")
        inspected = self.inspect()
        actions = {"power-cut": ("kill", "--signal", "KILL"), "stop": ("stop", "--time", "45")}
        if action == "start-existing":
            if inspected["State"]["Running"]:
                raise RuntimeError("The recorded local mock is already running")
            probe = capacity_image or self.record["image_id"]
            if capacity_image is not None:
                verify_capacity_image(
                    capacity_image, self.record.get("engine_architecture", "arm64")
                )
            if "engine_architecture" in self.record:
                require_capacity(probe, self.record["volume"], self.record["engine_architecture"])
            else:
                require_capacity(probe, self.record["volume"])
            arguments = ("start",)
        else:
            arguments = actions[action]
        docker(arguments + (self.record["container_id"],))

    def ssh_arguments(self) -> tuple[str, ...]:
        inspected = self.inspect()
        published = inspected["NetworkSettings"]["Ports"]["2222/tcp"][0]
        if published["HostIp"] != "127.0.0.1" or re.fullmatch(
            r"[0-9]{1,5}", published["HostPort"]
        ) is None:
            raise RuntimeError("The mock SSH endpoint must be loopback only")
        return (
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=45", "-o",
            "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "-o", "ProxyCommand=" + docker_ssh_proxy(inspected["Id"]),
            "-o", "IdentitiesOnly=yes", "-o", "HostKeyAlgorithms=ssh-ed25519", "-o",
            "HostKeyAlias=" + self.name, "-o", "StrictHostKeyChecking=accept-new", "-o",
            "UserKnownHostsFile=" + str(self.path.parent / "known_hosts"), "-i",
            str(self.path.parent / "identity"), "-p", published["HostPort"],
            "fixture@127.0.0.1",
        )

    def guest(self, arguments: tuple[str, ...]) -> str:
        return command(self.ssh_arguments() + (shlex.join(arguments),), self.path.parent)


def verify_capacity_image(reference: str, architecture: str = "arm64") -> None:
    if re.fullmatch(r"sha256:[0-9a-f]{64}", reference) is None:
        raise ValueError("An immutable local capacity image is required")
    image = json.loads(docker(("image", "inspect", reference)))[0]
    if (image["Id"], image["Os"], image["Architecture"], image["Config"]["User"]) != (
        reference, "linux", native_architecture(architecture), "10001:10001"
    ):
        raise ValueError("The capacity image must match the native engine and be non-root")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acceleration", choices=("tcg", "kvm"), default="tcg")
    parser.add_argument("--capacity-image", help="Immutable native utility for restart admission")
    parser.add_argument(
        "action", choices=("create", "inspect", "guest", "stop", "power-cut", "start-existing")
    )
    parser.add_argument("reference", help="Tool image for create; state.json for other actions")
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    selected = parser.parse_args()
    if selected.action == "create":
        print(create(selected.reference, selected.acceleration))
        return
    mock = LocalMock(Path(selected.reference))
    if selected.action == "inspect":
        print(json.dumps(mock.inspect(), indent=2))
    elif selected.action == "guest":
        print(mock.guest(tuple(selected.arguments)))
    else:
        mock.control(selected.action, selected.capacity_image)


if __name__ == "__main__":
    main()
