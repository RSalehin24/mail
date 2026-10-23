"""Prepare only a new mock disk offline, with filesystem checks before and after editing."""

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mock_profile import BASE_SHA512  # noqa: E402


def run(arguments: tuple[str, ...]) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed user-mode tools on the private guest disk.
        arguments, check=True, capture_output=True, text=True, timeout=300
    )
    return result.stdout


@dataclass(frozen=True)
class Partition:
    offset: int
    length: int


def root_partition(path: Path) -> Partition:
    with path.open("rb") as source:
        source.seek(512)
        header = source.read(512)
        if header[:8] != b"EFI PART":
            raise RuntimeError("The pinned fixture must have a GPT")
        first, count, size = struct.unpack_from("<QII", header, 72)
        if not 1 <= count <= 128 or size != 128:
            raise RuntimeError("Unexpected fixture partition table")
        source.seek(first * 512)
        entry = source.read(size)
    beginning, end = struct.unpack_from("<QQ", entry, 32)
    partition = Partition(beginning * 512, (end - beginning + 1) * 512)
    if (
        beginning == 0
        or partition.length <= 0
        or partition.offset + partition.length > path.stat().st_size
    ):
        raise RuntimeError("Invalid fixture root partition")
    return partition


def copy_bytes(reader, writer, length: int) -> None:
    while length:
        block = reader.read(min(length, 1024 * 1024))
        if not block:
            raise RuntimeError("Truncated fixture partition")
        writer.write(block)
        length -= len(block)
    writer.flush()
    os.fsync(writer.fileno())


def extract_partition(paths: tuple[Path, Path], partition: Partition) -> None:
    disk, filesystem = paths
    with disk.open("rb") as reader, filesystem.open("xb") as writer:
        reader.seek(partition.offset)
        copy_bytes(reader, writer, partition.length)


def install_partition(paths: tuple[Path, Path], partition: Partition) -> None:
    disk, filesystem = paths
    with filesystem.open("rb") as reader, disk.open("r+b") as writer:
        writer.seek(partition.offset)
        # Zero blocks must overwrite old bytes as well; seeking would preserve stale data.
        copy_bytes(reader, writer, partition.length)


def extract_sparse_partition(paths: tuple[Path, Path], partition: Partition) -> None:
    disk, filesystem = paths
    with disk.open("rb") as reader, filesystem.open("xb") as writer:
        reader.seek(partition.offset)
        remaining = partition.length
        while remaining:
            block = reader.read(min(remaining, 1024 * 1024))
            if not block:
                raise RuntimeError("Truncated fixture partition")
            # This destination is new: unwritten holes contain zeros, never stale bytes.
            if any(block):
                writer.write(block)
            else:
                writer.seek(len(block), os.SEEK_CUR)
            remaining -= len(block)
        writer.truncate(partition.length)
        writer.flush()
        os.fsync(writer.fileno())


def install_changed_partition(paths: tuple[Path, Path], partition: Partition) -> None:
    disk, filesystem = paths
    with filesystem.open("rb") as reader, disk.open("r+b") as writer:
        writer.seek(partition.offset)
        remaining = partition.length
        while remaining:
            block = reader.read(min(remaining, 1024 * 1024))
            if not block:
                raise RuntimeError("Truncated fixture partition")
            current = writer.read(len(block))
            if current != block:
                writer.seek(-len(current), os.SEEK_CUR)
                writer.write(block)
            remaining -= len(block)
        writer.flush()
        os.fsync(writer.fileno())


def check_filesystem(path: Path) -> None:
    result = subprocess.run(  # noqa: S603, S607 -- never a host device or mounted filesystem.
        ("/usr/sbin/e2fsck", "-f", "-y", str(path)),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError("Mock filesystem check failed: " + result.stdout[-2000:])


def replace_file(filesystem: Path, values: tuple[str, str]) -> str:
    destination, addition = values
    previous = run(("debugfs", "-R", "cat " + destination, str(filesystem)))
    content = previous + addition
    staged = filesystem.parent / Path(destination).name
    staged.write_text(content)
    run(("debugfs", "-w", "-R", "rm " + destination, str(filesystem)))
    run(("debugfs", "-w", "-R", f"write {staged} {destination}", str(filesystem)))
    actual = run(("debugfs", "-R", "cat " + destination, str(filesystem)))
    if actual != content:
        raise RuntimeError("Mock filesystem configuration readback failed")
    return hashlib.sha256(content.encode()).hexdigest()


def prepare(root: Path) -> None:
    if (root / "disk.qcow2").exists():
        raise RuntimeError("Offline preparation never replaces an existing guest disk")
    with (root / "base.qcow2").open("rb") as source:
        if hashlib.file_digest(source, "sha512").hexdigest() != BASE_SHA512:
            raise RuntimeError("Pinned mock base image changed")
    with tempfile.TemporaryDirectory(prefix="preparation-", dir=root) as directory:
        workspace = Path(directory)
        disk, filesystem = workspace / "disk.raw", workspace / "root.raw"
        run(
            (
                "qemu-img",
                "convert",
                "-t",
                "writeback",
                "-O",
                "raw",
                str(root / "base.qcow2"),
                str(disk),
            )
        )
        partition = root_partition(disk)
        extract_sparse_partition((disk, filesystem), partition)
        check_filesystem(filesystem)
        configurations = configure(filesystem)
        check_filesystem(filesystem)
        install_changed_partition((disk, filesystem), partition)
        publish(root, (workspace, disk))
    (root / "prepared-base-observation.json").write_text(
        json.dumps(configurations, indent=2) + "\n"
    )


def configure(filesystem: Path) -> dict[str, str]:
    return {
        "scope": "disposable software-emulated guest setup; not application qualification",
        "base_sha512": BASE_SHA512,
        "fstab_sha256": replace_file(filesystem, ("/etc/fstab", "\n/dev/vdb none swap sw 0 0\n")),
        "system_conf_sha256": replace_file(
            filesystem,
            (
                "/etc/systemd/system.conf",
                "\n# Software emulation only; application unit deadlines remain unchanged.\n"
                "DefaultTimeoutStartSec=900s\nDefaultDeviceTimeoutSec=900s\n",
            ),
        ),
    }


def publish(root: Path, paths: tuple[Path, Path]) -> None:
    workspace, disk = paths
    candidate = workspace / "candidate.qcow2"
    run(
        (
            "qemu-img",
            "convert",
            "-t",
            "writeback",
            "-f",
            "raw",
            "-O",
            "qcow2",
            str(disk),
            str(candidate),
        )
    )
    run(("qemu-img", "resize", str(candidate), "8G"))
    with candidate.open("rb") as source:
        os.fsync(source.fileno())
    os.link(candidate, root / "disk.qcow2")
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    os.umask(0o077)
    prepare(Path("/fixture"))
