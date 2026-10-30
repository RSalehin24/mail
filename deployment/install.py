"""Ubuntu 24.04 amd64 installer. Refuses unknown installations before any mutation."""

import argparse
import json
import os
import platform
import pwd
import re
import secrets
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any


def run(*arguments: str) -> str:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=300)  # noqa: S603 -- argv only; root installer.
    return result.stdout.strip()


def preflight(
    kind: str,
    root: Path,
    memory_mib: int,
    image: str,
    maddy_image: str | None,
    maddy_memory_mib: int | None,
    inspect_application: bool = True,
    backup_memory_mib: int | None = None,
) -> dict[str, int]:
    release = platform.freedesktop_os_release()
    if release.get("ID") != "ubuntu" or release.get("VERSION_ID") != "24.04":
        raise ValueError("Initial installer supports Ubuntu 24.04 LTS only")
    if platform.machine() != "x86_64" or os.geteuid() != 0:
        raise ValueError("Run as root on amd64")
    if root.exists() or root.is_symlink():
        raise ValueError("Existing installation: use explicit adoption or upgrade")
    for known in (
        "/etc/mail-admin",
        "/opt/maddy",
        "/var/lib/maddy",
        "/etc/mail-" + kind,
        "/opt/mail-" + kind,
    ):
        if Path(known).exists():
            raise ValueError("Existing mail installation requires adoption; no changes made")
    name = "mail-" + kind
    definitions = (
        name + ".service",
        name + "-maintenance.service",
        name + "-maintenance.timer",
        name + "-backup.service",
        name + "-backup.timer",
        name + "-helper.service",
    )
    if any((Path("/etc/systemd/system") / value).exists() for value in definitions):
        raise ValueError("Unknown existing service definition; no changes made")
    try:
        pwd.getpwnam(name)
    except KeyError:
        pass
    else:
        raise ValueError("Existing application service account requires adoption")
    if kind == "management" and any(
        Path(value).exists()
        for value in (
            "/var/lib/mail-management-store",
            "/var/lib/mail-management-helper",
        )
    ):
        raise ValueError("Existing mail storage requires adoption; no changes made")
    context = json.loads(run("/usr/bin/docker", "context", "inspect"))[0]
    endpoint = os.environ.get("DOCKER_HOST", context["Endpoints"]["docker"]["Host"])
    if not endpoint.startswith("unix://"):
        raise ValueError("A local Docker engine is required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("Select a locally loaded immutable application image ID")
    if inspect_application:
        inspection = json.loads(run("/usr/bin/docker", "image", "inspect", image))[0]
        if (inspection["Architecture"], inspection["Os"]) != ("amd64", "linux"):
            raise ValueError("Application image must be Linux amd64")
        label = inspection.get("Config", {}).get("Labels", {}) or {}
        if label.get("mail.application") != kind:
            raise ValueError("Select the matching application package image")
    for name in ("maddy", "mail-management", "mail-webmail", "mail-admin"):
        found = subprocess.run(  # noqa: S603 -- fixed Docker inspect argv.
            ("/usr/bin/docker", "inspect", name), capture_output=True, check=False, timeout=10
        )
        if found.returncode == 0:
            raise ValueError("Existing containers require adoption; no changes made")
    if kind == "management":
        if (
            maddy_image is None
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", maddy_image)
            or maddy_memory_mib is None
            or maddy_memory_mib < 1
        ):
            raise ValueError("Select an immutable Maddy 0.9.6 image and candidate memory limit")
        native = json.loads(run("/usr/bin/docker", "image", "inspect", maddy_image))[0]
        if (native["Architecture"], native["Os"]) != ("amd64", "linux"):
            raise ValueError("Maddy image must be Linux amd64")
    memory = {
        key.rstrip(":"): int(value.split()[0])
        for key, value in (
            line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
        )
    }
    active = memory_mib * (2 if kind == "management" else 1) + (maddy_memory_mib or 0)
    # Backups stop application/native writers; reserve the larger execution phase.
    needed = max(active, backup_memory_mib or memory_mib)
    # The integration listener has its own process budget; budgets are never pooled across hosts.
    if (
        min(memory_mib, memory["MemTotal"] // 1024) < 1
        or memory["MemAvailable"] < (needed + 128) * 1024
    ):
        raise ValueError("Insufficient available memory for selected limits and 128 MiB reserve")
    if shutil.disk_usage(root.parent).free < 3 * 1024**3:
        raise ValueError("At least 3 GiB free disk required")
    ports = (443, 25, 587, 993, 8444) if kind == "management" else (443,)
    for port in ports:
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", port))  # noqa: S104 -- test intended public port.
    if kind == "management" and not Path("/usr/bin/python3").is_file():
        raise ValueError("Install Python 3 and python3-venv first")
    return {"memory_total_kib": memory["MemTotal"], "memory_available_kib": memory["MemAvailable"]}


def compose(kind: str, root: Path, image: str, memory: int, uid: int, gid: int) -> dict[str, Any]:
    mounts: list[dict[str, Any]] = [
        {
            "type": "bind",
            "source": str(root),
            "target": "/state",
            "bind": {"create_host_path": False},
        }
    ]
    if kind == "management":
        mounts.append(
            {
                "type": "bind",
                "source": "/run/mail-management-helper",
                "target": "/run/mail-management-helper",
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
    service: dict[str, Any] = {
        "image": image,
        "pull_policy": "never",
        "user": f"{uid}:{gid}",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 100,
        "mem_limit": f"{memory}m",
        "restart": "unless-stopped",
        "volumes": mounts,
        "tmpfs": ["/tmp:size=33554432,mode=1777"],  # noqa: S108 -- bounded container tmpfs.
        "ports": ["443:8443"],
        "container_name": "mail-" + kind,
        "command": ["python", "-m", "applications.runtime", kind, "--root", "/state"],
        "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
    }
    result: dict[str, Any] = {"services": {"application": service}}
    if kind == "management":
        result["services"]["integration"] = {
            **service,
            "container_name": "mail-management-integration",
            "ports": ["8444:8444"],
            "command": service["command"] + ["--integration"],
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("management", "webmail"))
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--image", help="Defaults to this package’s immutable application image")
    parser.add_argument(
        "--memory-mib",
        type=int,
        required=True,
        help="Candidate application limit, to be measured during qualification",
    )
    parser.add_argument("--backup-memory-mib", type=int)
    parser.add_argument("--maddy-image")
    parser.add_argument("--maddy-memory-mib", type=int)
    parser.add_argument("--maddy-go-memory-mib", type=int)
    parser.add_argument("--hash-memory-kib", type=int, required=True)
    parser.add_argument("--http-concurrency", type=int, required=True)
    parser.add_argument("--attachment-spool-mib", type=int, default=128)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--qualification-only",
        action="store_true",
        help="Mark a separate synthetic test installation for host monitoring",
    )
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    from deployment.package import inspect_packaged_image, verify_package

    manifest = verify_package(package, args.kind)
    reference = args.image or manifest.get("image_reference", manifest["image"])
    root = Path("/var/lib/mail-" + args.kind)
    try:
        observed = inspect_packaged_image(package, manifest, reference)
    except subprocess.CalledProcessError:
        loaded = False
        args.image = manifest["image"]
    else:
        loaded = True
        args.image = observed["Id"]
    preflight(
        args.kind,
        root,
        args.memory_mib,
        args.image,
        args.maddy_image,
        args.maddy_memory_mib,
        inspect_application=loaded,
        backup_memory_mib=args.backup_memory_mib,
    )
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,251}[a-z0-9]", args.hostname):
        raise ValueError("Invalid hostname")
    if (
        not 8192 <= args.hash_memory_kib <= args.memory_mib * 1024
        or not 1 <= args.http_concurrency <= 32
        or not 10 <= args.attachment_spool_mib <= 512
    ):
        raise ValueError("Invalid hash memory or concurrency")
    if args.maddy_go_memory_mib is not None and not (
        0 < args.maddy_go_memory_mib < (args.maddy_memory_mib or 0)
    ):
        raise ValueError("Native Go memory target must be below its container limit")
    if args.check:
        print("Preflight passed; no changes made")
        return
    os.umask(0o077)
    if not loaded:
        run("/usr/bin/docker", "load", "--input", str(package / "image.tar"))
        observed = inspect_packaged_image(package, manifest, reference)
        args.image = observed["Id"]
        preflight(
            args.kind,
            root,
            args.memory_mib,
            args.image,
            args.maddy_image,
            args.maddy_memory_mib,
            backup_memory_mib=args.backup_memory_mib,
        )
    if manifest["kind"] != args.kind:
        raise ValueError("Wrong application package")
    account = "mail-" + args.kind
    # User and protected directories are created only after all preflight checks pass.
    run(
        "/usr/sbin/useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", account
    )
    uid = int(run("/usr/bin/id", "-u", account))
    gid = int(run("/usr/bin/id", "-g", account))
    root.mkdir(mode=0o700)
    resources = {
        "hash_memory_kib": args.hash_memory_kib,
        "hash_parallelism": 1,
        "http_concurrency": args.http_concurrency,
        "operation_timeout": 15,
        "attachment_spool_mib": args.attachment_spool_mib,
    }
    token = run(
        "/usr/bin/docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0:0",
        "--volume",
        str(root) + ":/state",
        "--entrypoint",
        "python",
        args.image,
        "-m",
        "applications.bootstrap",
        args.kind,
        "/state",
        args.hostname,
        "--resources",
        json.dumps(resources),
    )
    for target in [root, *root.rglob("*")]:
        if not target.is_symlink():
            os.chown(target, uid, gid)
    config = Path("/etc") / account
    config.mkdir(mode=0o700)
    (config / "compose.json").write_text(
        json.dumps(compose(args.kind, root, args.image, args.memory_mib, uid, gid))
    )
    release = Path("/opt") / account / manifest["version"]
    release.parent.mkdir(mode=0o700, exist_ok=True)
    shutil.copytree(package, release)
    run("/usr/bin/python3", "-m", "venv", str(release / ".venv"))
    run(
        str(release / ".venv/bin/pip"),
        "install",
        "--require-hashes",
        "-r",
        str(release / "requirements.lock"),
    )
    host = {
        "kind": args.kind,
        "root": str(root),
        "image": args.image,
        "uid": uid,
        "gid": gid,
        "version": manifest["version"],
        "release": str(release),
        "maddy_image": args.maddy_image,
        "maddy_memory_mib": args.maddy_memory_mib,
        "maddy_go_memory_mib": args.maddy_go_memory_mib,
        "memory_mib": args.memory_mib,
        "backup_memory_mib": args.backup_memory_mib or args.memory_mib,
        "adopted": False,
        "qualification_only": args.qualification_only,
    }
    (config / "host.json").write_text(json.dumps(host))
    (config / "backup.key").write_text(secrets.token_urlsafe(48))
    (config / "backup.key").chmod(0o600)
    if args.kind == "management":
        socket_root = Path("/run/mail-management-helper")
        socket_root.mkdir(mode=0o750)
        os.chown(socket_root, 0, gid)
    from deployment.units import install_units

    install_units(host, config)
    print("Setup URL: https://" + args.hostname + "/setup")
    print(
        "Bootstrap certificate SHA256: " + (root / "bootstrap-fingerprint.txt").read_text().strip()
    )
    print("Bootstrap certificate is temporary; verify it locally, then replace it in the wizard.")
    print("Single-use setup token (expires in 30 minutes): " + token)


if __name__ == "__main__":
    main()
