"""Create versioned, separately installable application archives from immutable local images."""

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from applications import VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def package(kind: str, image: str, output: Path) -> Path:
    inspection = json.loads(
        subprocess.check_output(  # noqa: S603 -- immutable local image selection.
            ("/usr/bin/docker", "image", "inspect", image), text=True, timeout=30
        )
    )[0]
    if (inspection["Architecture"], inspection["Os"]) != ("amd64", "linux"):
        raise ValueError("Native Linux amd64 image required")
    if inspection["Config"]["Labels"].get("mail.application") != kind:
        raise ValueError("Wrong application image")
    if inspection["Config"]["Labels"].get("org.opencontainers.image.version") != VERSION:
        raise ValueError("Application image and host package versions must match")
    destination = output / ("mail-" + kind + "-" + VERSION)
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    for directory in ("applications", "deployment", "backend", "helper", "host", "shared"):
        shutil.copytree(
            ROOT / directory,
            destination / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shutil.copyfile(ROOT / "requirements.lock", destination / "requirements.lock")
    shutil.copyfile(ROOT / "build/restic/0.19.1/restic-linux_amd64", destination / "restic")
    (destination / "restic").chmod(0o755)
    reference = "mail-" + kind + ":package-" + inspection["Id"].partition(":")[2]
    subprocess.run(("/usr/bin/docker", "tag", inspection["Id"], reference), check=True, timeout=30)  # noqa: S603 -- exact immutable source tag.
    subprocess.run(  # noqa: S603 -- argv only, fixed Docker save operation.
        ("/usr/bin/docker", "save", "--output", str(destination / "image.tar"), reference),
        check=True,
        timeout=300,
    )  # noqa: S603,S607
    (destination / "install.sh").write_text(
        '#!/bin/sh\nset -eu\ncd -- "$(dirname -- "$0")"\n'
        "exec /usr/bin/python3 -m deployment.install " + kind + ' "$@"\n'
    )
    (destination / "install.sh").chmod(0o755)
    manifest = {
        "kind": kind,
        "version": VERSION,
        "schema_version": 1,
        "image": inspection["Id"],
        "image_reference": reference,
        "files": {},
    }
    for path in sorted(destination.rglob("*")):
        if path.is_file():
            manifest["files"][str(path.relative_to(destination))] = digest(path)
    (destination / "package.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = output / (destination.name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as writer:
        writer.add(destination, arcname=destination.name)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        digest(archive) + "  " + archive.name + "\n"
    )
    return archive


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--management-image", required=True)
    parser.add_argument("--webmail-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for kind in ("management", "webmail"):
        print(package(kind, getattr(args, kind + "_image"), args.output))


if __name__ == "__main__":
    main()
