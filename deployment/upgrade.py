"""Upgrade one recognized application; preserve data, keys, routing and disabled rollout state."""

import argparse
import json
import os
import shutil
from pathlib import Path

from applications.host import command
from applications.schema import install
from backend.database import Database
from deployment.package import inspect_packaged_image, verify_package
from deployment.units import units
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


def upgrade(path: Path, package: Path) -> None:
    host = protected_json(path)
    manifest = verify_package(package, host["kind"])
    name = "mail-" + host["kind"]
    config = path.parent
    for filename, content in units(host, config).items():
        if (Path("/etc/systemd/system") / filename).read_text() != content:
            raise ServiceError("unknown_service_installation", 409)
    root = Path(host["root"])
    marker = json.loads((root / "installation.json").read_text())
    database = Database(root / (host["kind"] + ".db"), marker["installation_id"])
    release = Path("/opt") / name / manifest["version"]
    if release.exists():
        raise ServiceError("release_already_installed", 409)
    # Keep a concrete local rollback snapshot before schema changes; never touch the mail store.
    snapshot = config / ("pre-upgrade-" + manifest["version"] + ".db")
    database.snapshot(snapshot)
    shutil.copytree(package, release)
    command("/usr/bin/python3", "-m", "venv", str(release / ".venv"))
    command(
        str(release / ".venv/bin/pip"),
        "install",
        "--require-hashes",
        "-r",
        str(release / "requirements.lock"),
    )
    command("/usr/bin/docker", "load", "--input", str(package / "image.tar"))
    image = inspect_packaged_image(
        package, manifest, manifest.get("image_reference", manifest["image"])
    )

    command(
        "/usr/bin/systemctl",
        "stop",
        name + "-maintenance.timer",
        name + "-backup.timer",
        name + ".service",
    )
    if host["kind"] == "management":
        command("/usr/bin/systemctl", "stop", name + "-helper.service")
    install(database, host["kind"])
    with database.transaction() as connection:
        connection.execute("DELETE FROM sessions")
        connection.execute("DELETE FROM mailbox_sessions")
    before = dict(host)
    if host["kind"] == "management" and not host.get("adopted"):
        # Native Maddy remains running on its reviewed launcher during an app upgrade.
        host.setdefault("maddy_launcher_path", str(Path(host["release"]) / "host/maddy-launcher"))
    host.update(release=str(release), version=manifest["version"], image=image["Id"])
    marker["version"] = manifest["version"]
    atomic_json(root / "installation.json", marker)
    os.chown(root / "installation.json", host["uid"], host["gid"])
    compose_file = config / "compose.json"
    compose = json.loads(compose_file.read_text())
    for service in compose["services"].values():
        service["image"] = image["Id"]
    atomic_json(config / "pre-upgrade-host.json", before)
    atomic_json(compose_file, compose)
    atomic_json(path, host)
    for filename, content in units(host, config).items():
        (Path("/etc/systemd/system") / filename).write_text(content)
    command("/usr/bin/systemctl", "daemon-reload")
    if host["kind"] == "management":
        command("/usr/bin/systemctl", "start", name + "-helper.service")
    command(
        "/usr/bin/systemctl",
        "start",
        name + ".service",
        name + "-maintenance.timer",
        name + "-backup.timer",
    )
    print("Upgrade started; check HTTPS health and pairing. Prior data snapshot: " + str(snapshot))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=Path)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    upgrade(args.host, args.package)


if __name__ == "__main__":
    main()
