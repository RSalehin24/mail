"""Shared maintenance lock and durable unknown-writer exclusion."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from helper.journal import Journal
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError


@dataclass(frozen=True)
class MaintenanceLocks:
    maddy: Path
    policy: LockPolicy


class Coordination:
    def __init__(self, locks: MaintenanceLocks, journal: Journal) -> None:
        self.locks, self.journal = locks, journal

    def require_quiet(self) -> None:
        if self.journal.unresolved():
            raise ServiceError("helper_reconciliation_required", 409)

    @contextmanager
    def consistent(self) -> Iterator[None]:
        with HostLock(self.locks.maddy, self.locks.policy):
            self.require_quiet()
            yield
