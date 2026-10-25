"""Software-only guest inside an unprivileged, bounded fixture container."""

import functools
import hashlib
import http.server
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

if __package__:
    from .mock_profile import GUEST_SWAP_MIB, LOCAL_CONTAINER_MEMORY_MIB, LOCAL_GUEST_MEMORY_MIB
else:
    from mock_profile import GUEST_SWAP_MIB, LOCAL_CONTAINER_MEMORY_MIB, LOCAL_GUEST_MEMORY_MIB


def run(arguments: tuple[str, ...]) -> None:
    subprocess.run(arguments, check=True, timeout=60)  # noqa: S603 -- fixed guest-disk tools.


class PayloadHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *arguments: object) -> None:
        # Cloud-init fetches public fixture payloads. Keep its request paths out of evidence.
        return


def disk(root: Path) -> None:
    if (root / "repair-in-progress.json").exists():
        raise RuntimeError("An interrupted mock-only repair requires reconciliation")
    if not (root / "disk.qcow2").exists():
        subprocess.run(  # noqa: S603, S607 -- fixed offline guest preparation, no host devices.
            (sys.executable, str(Path(__file__).with_name("prepare.py"))), check=True, timeout=900
        )
    if not (root / "swap.raw").exists():
        with (root / "swap.raw").open("xb") as stream:
            stream.truncate(GUEST_SWAP_MIB * 1024 * 1024)
            stream.flush()
            os.fsync(stream.fileno())
        run(("mkswap", str(root / "swap.raw")))
    run(
        (
            "genisoimage",
            "-quiet",
            "-output",
            str(root / "seed.iso"),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(root / "user-data"),
            str(root / "meta-data"),
        )
    )


def guest_memory() -> str:
    memory = os.environ.get("MOCK_GUEST_MEMORY_MIB", "288")
    maximum = Path("/sys/fs/cgroup/memory.max").read_text().strip()
    if os.environ.get("MOCK_LOCAL_REPLACEMENT") == "1":
        if memory != str(LOCAL_GUEST_MEMORY_MIB) or maximum != str(
            LOCAL_CONTAINER_MEMORY_MIB * 1024 * 1024
        ):
            raise RuntimeError("The local replacement mock requires its exact memory bounds")
        return memory
    if memory not in {"224", "256", "288"} or maximum == "max":
        raise RuntimeError("A reviewed, bounded mock memory profile is required")
    if (int(memory) + 96) * 1024 * 1024 > int(maximum):
        raise RuntimeError("Mock memory profile leaves insufficient emulator overhead")
    return memory


def qemu_arguments(memory: str) -> tuple[str, ...]:
    acceleration = os.environ.get("MOCK_ACCELERATION", "tcg")
    if acceleration not in {"tcg", "kvm"}:
        raise RuntimeError("Unknown guest acceleration")
    if acceleration == "kvm" and os.environ.get("MOCK_LOCAL_REPLACEMENT") != "1":
        raise RuntimeError("KVM requires the explicit local replacement profile")
    return (
        "qemu-system-x86_64",
        "-machine",
        "q35",
        "-accel",
        "kvm" if acceleration == "kvm" else "tcg,thread=single,tb-size=32",
        "-cpu",
        "host" if acceleration == "kvm" else "max",
        "-smp",
        "1",
        "-m",
        memory,
        "-display",
        "none",
        "-serial",
        "stdio",
        "-monitor",
        "none",
        "-rtc",
        "base=utc,clock=host",
        "-object",
        "iothread,id=fixture-io,thread-pool-min=1,thread-pool-max=2",
        "-drive",
        "file=/fixture/disk.qcow2,if=none,id=fixture-disk,format=qcow2,cache=writeback",
        "-device",
        "virtio-blk-pci,drive=fixture-disk,iothread=fixture-io",
        "-drive",
        "file=/fixture/swap.raw,if=none,id=fixture-swap,format=raw,cache=writeback",
        "-device",
        "virtio-blk-pci,drive=fixture-swap,iothread=fixture-io",
        "-drive",
        "file=/fixture/seed.iso,if=none,id=fixture-seed,format=raw,readonly=on",
        "-device",
        "virtio-blk-pci,drive=fixture-seed,iothread=fixture-io",
        "-nic",
        "user,model=virtio-net-pci,hostfwd=tcp:0.0.0.0:2222-:22",
    )


def main() -> None:
    os.umask(0o077)
    root = Path("/fixture")
    memory = guest_memory()
    disk(root)
    engine = Path("/opt/payload/docker-engine.tar.gz")
    with engine.open("rb") as reader:
        print("engine_sha256=" + hashlib.file_digest(reader, "sha256").hexdigest(), flush=True)
    handler = functools.partial(PayloadHandler, directory="/opt/payload")
    server = http.server.ThreadingHTTPServer(("0.0.0.0", 8000), handler)  # noqa: S104 -- private guest NAT, never published.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    arguments = qemu_arguments(memory)
    process = subprocess.Popen(arguments, close_fds=True)  # noqa: S603 -- no host devices, socket or production paths.
    signal.signal(signal.SIGTERM, lambda *_: process.terminate())
    signal.signal(signal.SIGINT, lambda *_: process.terminate())
    try:
        raise SystemExit(process.wait())
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
