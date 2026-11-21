"""Restart the dashboard when Maddy renews the HTTPS certificate it shares."""

import os
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path

CHECK_INTERVAL_SECONDS = 6 * 3600


def _terminate() -> None:
    # The container's restart policy brings the process back with the new certificate.
    os.kill(os.getpid(), signal.SIGTERM)


def restart_when_replaced(
    certificate: Path,
    interval: float = CHECK_INTERVAL_SECONDS,
    restart: Callable[[], None] = _terminate,
) -> threading.Thread:
    loaded = certificate.stat().st_mtime_ns

    def watch() -> None:
        while True:
            time.sleep(interval)
            if certificate.stat().st_mtime_ns != loaded:
                restart()
                return

    thread = threading.Thread(target=watch, name="certificate-watch", daemon=True)
    thread.start()
    return thread
