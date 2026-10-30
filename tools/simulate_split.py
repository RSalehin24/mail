"""Two separate Ubuntu 24.04/1 GiB guests inside unprivileged Docker wrappers."""

# Exact fixture argv and bounded private container tmpfs; no shell interpolation.
# ruff: noqa: S603, S108

import argparse
import hashlib
import ipaddress
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_mock_host import CONTAINERD_UNIT, DOCKER_UNIT  # noqa: E402
from tools.ssh_proxy import STREAM_PROXY  # noqa: E402

TOOL_IMAGE = "mail-admin:split-guest"
BASE_DIGEST = "04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94"


def docker(*args):
    return subprocess.check_output(("/usr/bin/docker", *args), text=True, timeout=300).strip()


def create(root: Path, base: Path) -> None:
    if root.exists():
        raise ValueError("Use a fresh simulation directory")
    with base.open("rb") as reader:
        if hashlib.file_digest(reader, "sha256").hexdigest() != BASE_DIGEST:
            raise ValueError("Ubuntu base checksum mismatch")
    if not os.access("/dev/kvm", os.R_OK | os.W_OK):
        raise ValueError("This faithful simulation requires the available local KVM device")
    root.mkdir(mode=0o700)
    subprocess.run(
        ("/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(root / "identity")),
        check=True,
        timeout=10,
    )
    key = (root / "identity.pub").read_text().strip()
    identifier = uuid.uuid4().hex[:12]
    network = "mail-split-simulation-" + identifier
    docker("network", "create", "--label", "mail-admin.fixture=true", network)
    net = json.loads(docker("network", "inspect", network))[0]
    subnet = ipaddress.ip_network(net["IPAM"]["Config"][0]["Subnet"])
    addresses = {
        "management": str(subnet[2]),
        "webmail": str(subnet[3]),
        "provider": str(subnet[4]),
    }
    engine = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--entrypoint",
        "python",
        TOOL_IMAGE,
        "-c",
        "import hashlib; from pathlib import Path; "
        "print(hashlib.file_digest(Path('/opt/payload/docker-engine.tar.gz').open('rb'),"
        "'sha256').hexdigest())",
    )
    record = {
        "scope": "docker-contained-kvm-simulation; not production qualification",
        "id": identifier,
        "network": network,
        "addresses": addresses,
        "guests": {},
        "base_sha256": BASE_DIGEST,
        "base_image": str(base.resolve()),
        "guest_memory_mib": 1024,
        "wrapper_memory_mib": 1280,
        "vcpus_per_guest": 1,
    }
    (root / "state.json").write_text(json.dumps(record, indent=2) + "\n")
    for kind in ("management", "webmail"):
        name = "mail-split-" + kind + "-" + identifier
        volume = name + "-disk"
        docker("volume", "create", "--label", "mail-admin.fixture=true", volume)
        content = {
            "users": [
                {
                    "name": "fixture",
                    "groups": ["sudo"],
                    "shell": "/bin/bash",
                    "sudo": "ALL=(ALL) NOPASSWD:ALL",
                    "lock_passwd": True,
                    "ssh_authorized_keys": [key],
                }
            ],
            "ssh_pwauth": False,
            "disable_root": True,
            "package_update": True,
            "packages": ["python3-venv", "curl", "iptables", "apparmor", "ca-certificates"],
            "write_files": [
                {"path": "/etc/systemd/system/docker.service", "content": DOCKER_UNIT},
                {"path": "/etc/systemd/system/containerd.service", "content": CONTAINERD_UNIT},
                {
                    "path": "/etc/docker/daemon.json",
                    "content": json.dumps(
                        {
                            "dns": [addresses["provider"]],
                            "bip": "10.201.0.1/24",
                            "default-address-pools": [{"base": "10.200.0.0/16", "size": 24}],
                        }
                    ),
                },
                {"path": "/etc/mail-lab-simulation", "content": identifier},
                {
                    "path": "/etc/systemd/journald.conf.d/simulation.conf",
                    "content": "[Journal]\nSystemMaxUse=32M\nRuntimeMaxUse=8M\n",
                },
            ],
            "runcmd": [
                [
                    "/bin/sh",
                    "-c",
                    "set -eu\n"
                    "curl --fail --silent http://10.0.2.2:8000/docker-engine.tar.gz "
                    "-o /var/tmp/engine.tgz\n"
                    f"printf '%s  %s\\n' '{engine}' /var/tmp/engine.tgz | sha256sum -c -\n"
                    "tar -xzf /var/tmp/engine.tgz -C /usr/local/bin\n"
                    "ln -s /usr/local/bin/docker /usr/bin/docker\n"
                    "mkdir -p /usr/local/libexec/docker/cli-plugins\n"
                    "cp /usr/local/bin/cli-plugins/docker-compose "
                    "/usr/local/libexec/docker/cli-plugins/\n"
                    "swapoff -a\nsystemctl daemon-reload\nsystemctl enable --now docker.service\n"
                    "touch /var/lib/mail-simulation-ready\n",
                ]
            ],
        }
        payload = {
            "user-data": "#cloud-config\n" + json.dumps(content),
            "meta-data": json.dumps({"instance-id": name, "local-hostname": name}),
        }
        writer = (
            "import json,os,sys; from pathlib import Path; root=Path('/fixture'); "
            "os.chown(root,10001,10001); "
            "[(root/name).write_text(value) for name,value in json.load(sys.stdin).items()]; "
            "[os.chown(p,10001,10001) for p in root.iterdir()]"
        )
        subprocess.run(
            (
                "/usr/bin/docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--user",
                "0:0",
                "--volume",
                volume + ":/fixture",
                "--entrypoint",
                "python",
                TOOL_IMAGE,
                "-c",
                writer,
            ),
            input=json.dumps(payload),
            text=True,
            check=True,
            timeout=30,
        )
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--label",
            "mail-admin.fixture=true",
            "--network",
            network,
            "--ip",
            addresses[kind],
            "--memory",
            "1280m",
            "--memory-swap",
            "1280m",
            "--cpus",
            "1",
            "--pids-limit",
            "100",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--device",
            "/dev/kvm:/dev/kvm:rw",
            "--tmpfs",
            "/tmp:size=8388608,mode=1777",
            "--publish",
            "127.0.0.1::22",
            "--volume",
            volume + ":/fixture",
            "--mount",
            "type=bind,src=" + str(base.resolve()) + ",dst=/base.img,readonly",
            "--mount",
            "type=bind,src="
            + str(ROOT / "tools/vm/split_guest.py")
            + ",dst=/opt/split_guest.py,readonly",
            "--env",
            "SIMULATION_KIND=" + kind,
            "--entrypoint",
            "python",
        ]
        for alias in (
            ["manage.split.test", "mail.split.test"]
            if kind == "management"
            else ["webmail.split.test"]
        ):
            args += ["--network-alias", alias]
        args += [TOOL_IMAGE, "/opt/split_guest.py"]
        docker(*args)
        inspected = json.loads(docker("inspect", name))[0]
        record["guests"][kind] = {
            "container": name,
            "container_id": inspected["Id"],
            "volume": volume,
            "ssh_port": int(inspected["NetworkSettings"]["Ports"]["22/tcp"][0]["HostPort"]),
        }
        (root / "state.json").write_text(json.dumps(record, indent=2) + "\n")
    print(root / "state.json")


def ssh_arguments(root: Path, kind: str) -> list[str]:
    state = json.loads((root / "state.json").read_text())
    guest = state["guests"][kind]
    current = json.loads(docker("inspect", guest["container"]))[0]
    if (
        current["Id"] != guest["container_id"]
        or current["Config"]["Labels"].get("mail-admin.fixture") != "true"
    ):
        raise ValueError("Simulation identity changed")
    proxy = shlex.join(
        (
            "/usr/bin/docker",
            "exec",
            "-i",
            guest["container_id"],
            "python",
            "-c",
            "exec(bytes.fromhex('" + STREAM_PROXY.encode().hex() + "'))",
            "22",
        )
    )
    return [
        "/usr/bin/ssh",
        "-o",
        "ProxyCommand=" + proxy,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile=" + str(root / "known_hosts"),
        "-o",
        "HostKeyAlias=" + guest["container"],
        "-i",
        str(root / "identity"),
        "-p",
        str(guest["ssh_port"]),
        "fixture@127.0.0.1",
    ]


def configuration(root: Path, fixture: Path) -> None:
    """Use the same qualification harness with pinned SSH and private lab DNS."""
    if fixture.stat().st_mode & 0o077:
        raise ValueError("Use a mode-0600 synthetic configuration")
    values = json.loads(fixture.read_text())
    if values.get("scope") != "isolated-two-host-mail-qualification":
        raise ValueError("An isolated qualification configuration is required")
    state = json.loads((root / "state.json").read_text())
    definitions = []
    for kind in ("management", "webmail"):
        arguments = ssh_arguments(root, kind)
        proxy = next(value for value in arguments if value.startswith("ProxyCommand="))
        definitions.append(
            "Host split-" + kind + "\n"
            " HostName 127.0.0.1\n User fixture\n"
            " IdentityFile " + shlex.quote(str((root / "identity").resolve())) + "\n"
            " UserKnownHostsFile " + shlex.quote(str((root / "known_hosts").resolve())) + "\n"
            " HostKeyAlias " + state["guests"][kind]["container"] + "\n"
            " StrictHostKeyChecking yes\n " + proxy.replace("=", " ", 1) + "\n"
        )
        values[kind + "_ssh"] = "fixture@split-" + kind
    path = root / "ssh-config"
    path.write_text("\n".join(definitions))
    path.chmod(0o600)
    values.update(
        ssh_config=str(path.resolve()),
        simulation=True,
        simulation_dns={
            "manage.split.test": state["addresses"]["management"],
            "mail.split.test": state["addresses"]["management"],
            "webmail.split.test": state["addresses"]["webmail"],
            "relay.split.test": state["addresses"]["provider"],
        },
    )
    fixture.write_text(json.dumps(values, indent=2) + "\n")
    print(
        "Private SSH configuration written. Simulation evidence cannot enable production rollout."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("create", "provider", "guest", "copy", "configuration", "stop")
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--kind", choices=("management", "webmail"))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--provider-image", default="mail-admin:split-qualification")
    args, arguments = parser.parse_known_args()
    args.arguments = arguments[1:] if arguments[:1] == ["--"] else arguments
    os.umask(0o077)
    if args.action == "create":
        create(args.root, args.base or ROOT / "build/split-simulation/ubuntu-24.04.img")
    elif args.action == "provider":
        from tools.vm.split_provider_setup import prepare

        prepare(args.root, args.provider_image)
    elif args.action == "guest":
        result = subprocess.run(
            ssh_arguments(args.root, args.kind) + [shlex.join(args.arguments)], check=False
        )
        raise SystemExit(result.returncode)
    elif args.action == "copy":
        ssh = ssh_arguments(args.root, args.kind)
        index = ssh.index("-p")
        ssh[index] = "-P"
        subprocess.run(
            ["/usr/bin/scp", *ssh[1:-1], args.arguments[0], ssh[-1] + ":" + args.arguments[1]],
            check=True,
            timeout=300,
        )
    elif args.action == "configuration":
        if args.fixture is None:
            parser.error("configuration requires --fixture")
        configuration(args.root, args.fixture)
    else:
        state = json.loads((args.root / "state.json").read_text())
        for kind, guest in state["guests"].items():
            subprocess.run(
                ssh_arguments(args.root, kind) + ["sudo -n /usr/sbin/poweroff"],
                capture_output=True,
                check=False,
                timeout=15,
            )
            docker("stop", "--time", "30", guest["container_id"])


if __name__ == "__main__":
    main()
