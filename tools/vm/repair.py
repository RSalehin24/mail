"""Explicit stopped-mock disk repair; preserve the original disk and check the copy."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

if __package__:
    from .prepare import (
        check_filesystem,
        extract_sparse_partition,
        install_changed_partition,
        root_partition,
        run,
    )
else:
    from prepare import (
        check_filesystem,
        extract_sparse_partition,
        install_changed_partition,
        root_partition,
        run,
    )


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_unit(filesystem: Path, values: tuple[str, str]) -> None:
    name, content = values
    if name not in {"docker.service", "containerd.service"}:
        raise RuntimeError("Only the two guest fixture engine units may be repaired")
    destination = "/etc/systemd/system/" + name
    staged = filesystem.parent / name
    staged.write_text(content)
    run(("debugfs", "-w", "-R", "rm " + destination, str(filesystem)))
    run(("debugfs", "-w", "-R", f"write {staged} {destination}", str(filesystem)))
    for field, value in (("uid", "0"), ("gid", "0"), ("mode", "0100644")):
        instruction = f"set_inode_field {destination} {field} {value}"
        run(("debugfs", "-w", "-R", instruction, str(filesystem)))
    if run(("debugfs", "-R", "cat " + destination, str(filesystem))) != content:
        raise RuntimeError("Guest unit repair readback failed")


def repair(root: Path) -> None:
    os.umask(0o077)
    original = root / "failed-fifth-boot.qcow2"
    if original.exists():
        raise RuntimeError("Never overwrite a preserved mock disk")
    configuration = json.loads((root / "repair-units.json").read_bytes())
    intent = root / "repair-in-progress.json"
    with intent.open("x") as record:
        json.dump(
            {"scope": "stopped isolated mock only", "source_sha256": digest(root / "disk.qcow2")},
            record,
        )
        record.flush()
        os.fsync(record.fileno())
    with tempfile.TemporaryDirectory(prefix="repair-", dir=root) as directory:
        workspace = Path(directory)
        disk, filesystem = workspace / "disk.raw", workspace / "root.raw"
        run(
            ("qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(root / "disk.qcow2"), str(disk))
        )
        partition = root_partition(disk)
        extract_sparse_partition((disk, filesystem), partition)
        check_filesystem(filesystem)
        previous = run(("debugfs", "-R", "cat /etc/systemd/system/docker.service", str(filesystem)))
        if hashlib.sha256(previous.encode()).hexdigest() != configuration["original_docker_sha256"]:
            raise RuntimeError("The recorded original mock Docker unit changed")
        for name, content in configuration["units"].items():
            write_unit(filesystem, (name, content))
        check_filesystem(filesystem)
        install_changed_partition((disk, filesystem), partition)
        publish(root, (workspace, disk))
    (root / "sixth-boot-repair.json").write_text(
        json.dumps(
            {
                "scope": "fixture engine setup; not application qualification",
                "preserved_source_sha256": digest(original),
                "repaired_disk_sha256": digest(root / "disk.qcow2"),
                "configuration_sha256": digest(root / "repair-units.json"),
            },
            indent=2,
        ) + "\n"
    )
    intent.unlink()
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print("Repaired only the stopped mock; original fifth-boot disk preserved", flush=True)


def publish(root: Path, paths: tuple[Path, Path]) -> None:
    workspace, disk = paths
    candidate = workspace / "candidate.qcow2"
    run(("qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(disk), str(candidate)))
    with candidate.open("rb") as source:
        os.fsync(source.fileno())
    os.rename(root / "disk.qcow2", root / "failed-fifth-boot.qcow2")
    os.rename(candidate, root / "disk.qcow2")


if __name__ == "__main__":
    repair(Path("/fixture"))
