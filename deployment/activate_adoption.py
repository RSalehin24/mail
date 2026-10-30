"""Activate a reviewed migration export without changing Maddy configuration or incoming routing."""

import argparse
import os
import platform
import shutil
import socket
from pathlib import Path

from applications.host import command
from applications.setup import validate_tls
from deployment.install import compose
from deployment.package import inspect_packaged_image, verify_package
from deployment.units import install_units
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("management", "webmail"))
    parser.add_argument("export", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--legacy-deployment", type=Path)
    parser.add_argument("--fullchain", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--memory-mib", type=int, required=True)
    parser.add_argument(
        "--maddy-memory-mib", type=int, help="Explicit transient native maintenance budget"
    )
    args = parser.parse_args()
    release_info = platform.freedesktop_os_release()
    if (
        os.geteuid() != 0
        or platform.machine() != "x86_64"
        or release_info.get("ID") != "ubuntu"
        or release_info.get("VERSION_ID") != "24.04"
    ):
        raise ServiceError("root_ubuntu_24_amd64_required", 400)
    os.umask(0o077)
    root = Path("/var/lib/mail-" + args.kind)
    config = Path("/etc/mail-" + args.kind)
    name = "mail-" + args.kind
    if config.exists() or root.exists() and root.resolve() != args.export.resolve():
        raise ServiceError("unknown_existing_installation", 409)
    adoption = protected_json(args.export / "adoption.json")
    marker = protected_json(args.export / "installation.json")
    site = protected_json(args.export / "site.json")
    if marker["kind"] != args.kind or adoption[args.kind + "_id"] != marker["installation_id"]:
        raise ServiceError("adoption_identity_mismatch", 409)
    manifest = verify_package(args.package, args.kind)
    release = Path("/opt") / name / manifest["version"]
    if release.exists():
        raise ServiceError("unknown_existing_release", 409)
    # The old dashboard must be stopped separately; Maddy remains running throughout activation.
    for port in (443, 8444) if args.kind == "management" else (443,):
        with socket.socket() as listener:
            listener.bind(("0.0.0.0", port))  # noqa: S104 -- validate intended public ports.
    memory = {
        line.split(":")[0]: int(line.split()[1])
        for line in Path("/proc/meminfo").read_text().splitlines()
    }
    requested = args.memory_mib * (2 if args.kind == "management" else 1) + 128
    if args.memory_mib <= 0 or memory["MemAvailable"] < requested * 1024:
        raise ServiceError("insufficient_available_memory", 409)
    if shutil.disk_usage(args.export).free < 3 * 1024**3:
        raise ServiceError("insufficient_free_disk", 409)
    deployment = None
    if args.kind == "management":
        if args.legacy_deployment is None or not args.maddy_memory_mib or args.maddy_memory_mib < 1:
            parser.error(
                "Management adoption requires the original deployment and a native memory budget"
            )
        os.environ["MAIL_CONFIG"] = str(args.export / "site.json")
        from helper.maddy import DockerMaddy

        deployment = protected_json(args.legacy_deployment)
        DockerMaddy(deployment).validate()
    validate_tls(
        args.export,
        args.fullchain.read_text(),
        args.private_key.read_text(),
        [site["hostname"]] + ([site["mail_hostname"]] if args.kind == "management" else []),
    )
    if args.export.resolve() != root.resolve():
        shutil.copytree(args.export, root, symlinks=True)
    command(
        "/usr/sbin/useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", name
    )
    uid = int(command("/usr/bin/id", "-u", name).strip())
    gid = int(command("/usr/bin/id", "-g", name).strip())
    for target in [root, *root.rglob("*")]:
        if not target.is_symlink():
            os.chown(target, uid, gid)
    config.mkdir(mode=0o700)
    shutil.copytree(args.package, release)
    command("/usr/bin/python3", "-m", "venv", str(release / ".venv"))
    command(
        str(release / ".venv/bin/pip"),
        "install",
        "--require-hashes",
        "-r",
        str(release / "requirements.lock"),
    )
    command("/usr/bin/docker", "load", "--input", str(args.package / "image.tar"))
    image = inspect_packaged_image(
        args.package, manifest, manifest.get("image_reference", manifest["image"])
    )

    host = {
        "kind": args.kind,
        "root": str(root),
        "uid": uid,
        "gid": gid,
        "release": str(release),
        "version": manifest["version"],
        "image": image["Id"],
        "memory_mib": args.memory_mib,
        "adopted": args.kind == "management",
        "maddy_image": deployment["inputs"]["maddy_image_id"] if deployment else None,
        "maddy_memory_mib": args.maddy_memory_mib,
        "qualification_only": False,
        "legacy_deployment": str(args.legacy_deployment) if args.legacy_deployment else None,
    }
    import secrets

    (config / "backup.key").write_text(secrets.token_urlsafe(48))
    (config / "backup.key").chmod(0o600)
    atomic_json(config / "host.json", host)
    atomic_json(
        config / "compose.json",
        compose(args.kind, root, image["Id"], args.memory_mib, uid, gid),
    )
    if args.kind == "management":
        path = Path("/run/mail-management-helper")
        path.mkdir(mode=0o750, exist_ok=False)
        os.chown(path, 0, gid)
    install_units(host, config)
    print("Adoption activated. Maddy data and routing were preserved. Webmail remains disabled.")


if __name__ == "__main__":
    main()
