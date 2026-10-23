"""Fetch only the approved, pinned Restic release and verify before executing it."""

import argparse
import bz2
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

VERSION = "0.19.1"
ASSETS = {
    "darwin_arm64": "7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143",
    "linux_amd64": "f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c",
}
EXECUTABLES = {
    "darwin_arm64": "06582569ff2f10e1935a6f12187c76db02f0bae99e6e54098f2cdc374766d768",
    "linux_amd64": "20d4142678d0d95ec11a4759def1b73fd9190abc9ca19e4b62d067c0b387e639",
}
ROOT = Path(__file__).resolve().parents[1]


def fetch(platform: str) -> Path:
    directory = ROOT / "build/restic" / VERSION
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / ("restic-" + platform)
    record = path.with_suffix(".json")
    if path.exists():
        metadata = json.loads(record.read_bytes())
        if hashlib.sha256(path.read_bytes()).hexdigest() != EXECUTABLES[platform]:
            raise RuntimeError("Existing Restic executable changed")
        if metadata["asset_sha256"] != ASSETS[platform] or metadata["version"] != VERSION:
            raise RuntimeError("Existing Restic provenance changed")
        return path
    url = (
        "https://github.com/restic/restic/releases/download/v" + VERSION
        + "/restic_" + VERSION + "_" + platform + ".bz2"
    )
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 -- fixed HTTPS release.
        compressed = response.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(compressed).hexdigest() != ASSETS[platform]:
        raise RuntimeError("Restic release checksum mismatch")
    unpacked = bz2.BZ2Decompressor().decompress(compressed, max_length=64 * 1024 * 1024)
    if len(unpacked) == 64 * 1024 * 1024:
        raise RuntimeError("Restic executable exceeds its size bound")
    if hashlib.sha256(unpacked).hexdigest() != EXECUTABLES[platform]:
        raise RuntimeError("Restic executable checksum mismatch")
    descriptor, temporary = tempfile.mkstemp(prefix=".restic-", dir=directory)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(unpacked)
        writer.flush()
        os.fsync(writer.fileno())
        os.fchmod(writer.fileno(), 0o755)
    os.rename(temporary, path)
    record.write_text(json.dumps({
        "version": VERSION, "platform": platform, "source": url,
        "asset_sha256": ASSETS[platform],
        "executable_sha256": hashlib.sha256(unpacked).hexdigest(),
        "verification": "SHA-256 pinned to official GitHub release asset; no PGP claim",
    }, indent=2) + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=tuple(ASSETS))
    arguments = parser.parse_args()
    print(fetch(arguments.platform))


if __name__ == "__main__":
    main()
