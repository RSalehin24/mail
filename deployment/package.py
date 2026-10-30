"""Standard-library-only integrity checks used before installer mutations."""

import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any, cast


def verify_package(package: Path, kind: str) -> dict[str, Any]:
    manifest = json.loads((package / "package.json").read_text())
    if manifest["kind"] != kind or manifest["schema_version"] != 1:
        raise ValueError("Incompatible application package")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["version"]):
        raise ValueError("Invalid package version")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest["image"]):
        raise ValueError("Invalid package image")
    for name, expected in manifest["files"].items():
        path = package / name
        if not path.resolve().is_relative_to(package.resolve()) or path.is_symlink():
            raise ValueError("Unsafe package path")
        hasher = hashlib.sha256()
        with path.open("rb") as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                hasher.update(block)
        if hasher.hexdigest() != expected:
            raise ValueError("Package hash mismatch: " + name)
    return cast(dict[str, Any], manifest)


def inspect_packaged_image(
    package: Path, manifest: dict[str, Any], reference: str
) -> dict[str, Any]:
    """Verify immutable filesystem/configuration across Docker storage backends."""
    with tarfile.open(package / "image.tar") as archive:
        index_stream = archive.extractfile("manifest.json")
        if index_stream is None:
            raise ValueError("Image manifest missing")
        index = json.loads(index_stream.read(1024 * 1024))
        if len(index) != 1:
            raise ValueError("One packaged application image required")
        stream = archive.extractfile(index[0]["Config"])
        if stream is None:
            raise ValueError("Image configuration missing")
        expected = json.loads(stream.read(2 * 1024 * 1024))
    image = json.loads(
        subprocess.check_output(  # noqa: S603 -- read-only exact image inspection.
            ("/usr/bin/docker", "image", "inspect", reference),
            text=True,
            timeout=30,
        )
    )[0]
    if (
        image["Architecture"] != expected["architecture"]
        or image["Os"] != expected["os"]
        or image["RootFS"]["Layers"] != expected["rootfs"]["diff_ids"]
    ):
        raise ValueError("Packaged image filesystem mismatch")
    actual_config, expected_config = image["Config"], expected["config"]
    for key in set(actual_config) | set(expected_config):
        actual, wanted = actual_config.get(key), expected_config.get(key)
        if actual != wanted and not (
            actual in (None, False, "", [], {}) and wanted in (None, False, "", [], {})
        ):
            raise ValueError("Packaged image configuration mismatch: " + key)
    return cast(dict[str, Any], image)
