"""Boot a private Ubuntu guest inside a bounded unprivileged Docker fixture."""

# Fixed tools in the pinned QEMU fixture image, listening only on its private network.
# ruff: noqa: S603, S607, S104

import functools
import http.server
import os
import signal
import subprocess
import threading
from pathlib import Path


def main() -> None:
    root = Path("/fixture")
    if not (root / "disk.qcow2").exists():
        subprocess.run(
            (
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-b",
                "/base.img",
                "-F",
                "qcow2",
                str(root / "disk.qcow2"),
                "24G",
            ),
            check=True,
            timeout=30,
        )
    subprocess.run(
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
        ),
        check=True,
        timeout=30,
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory="/opt/payload")
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", 8000), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ports = [22, 443] + (
        [25, 587, 993, 8444] if os.environ["SIMULATION_KIND"] == "management" else []
    )
    forwards = ",".join(f"hostfwd=tcp:0.0.0.0:{port}-:{port}" for port in ports)
    process = subprocess.Popen(
        (
            "qemu-system-x86_64",
            "-enable-kvm",
            "-cpu",
            "host",
            "-m",
            "1024",
            "-smp",
            "1",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-rtc",
            "base=utc,clock=host",
            "-drive",
            "file=/fixture/disk.qcow2,if=virtio,format=qcow2,cache=writeback",
            "-drive",
            "file=/fixture/seed.iso,format=raw,media=cdrom,readonly=on",
            "-nic",
            "user,model=virtio-net-pci," + forwards,
        )
    )
    signal.signal(signal.SIGTERM, lambda *_: process.terminate())
    signal.signal(signal.SIGINT, lambda *_: process.terminate())
    try:
        raise SystemExit(process.wait())
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
