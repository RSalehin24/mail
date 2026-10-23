"""Stable root-owned advisory locks; callers acquire renewal before maintenance."""

import fcntl
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from shared.errors import ServiceError


@dataclass(frozen=True)
class LockPolicy:
    timeout: float
    owner: int = 0


class HostLock:
    def __init__(self, path: Path, policy: LockPolicy) -> None:
        self.path, self.policy = path, policy
        self.descriptor: int | None = None

    def __enter__(self) -> "HostLock":
        descriptor = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        self.descriptor = descriptor
        metadata = os.fstat(descriptor)
        valid = (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == self.policy.owner
            and stat.S_IMODE(metadata.st_mode) == 0o600
        )
        if not valid:
            self._close()
            raise ServiceError("unsafe_lock_file", 503)
        self._acquire()
        return self

    def _acquire(self) -> None:
        if self.descriptor is None:
            raise ServiceError("lock_not_open", 503)
        deadline = time.monotonic() + self.policy.timeout
        while True:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._close()
                    raise ServiceError("maintenance_busy", 503, 1) from None
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def _close(self) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()
