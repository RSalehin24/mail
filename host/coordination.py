"""Shared two-lock maintenance scope and durable unknown-writer exclusion."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from helper.journal import Journal
from host.certificates import Generations
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError


@dataclass(frozen=True)
class MaintenanceLocks:
    renewal: Path
    maddy: Path
    policy: LockPolicy


class Coordination:
    def __init__(self, locks: MaintenanceLocks, dependencies: tuple[Journal, Generations]) -> None:
        self.locks = locks
        self.journal, self.generations = dependencies

    def require_quiet(self) -> None:
        if self.journal.unresolved():
            raise ServiceError("helper_reconciliation_required", 409)
        run = self.generations.read().get("acme_run")
        if run and run["state"] not in {"completed", "not_started"}:
            raise ServiceError("acme_completion_unknown", 409)

    @contextmanager
    def consistent(self) -> Iterator[None]:
        with HostLock(self.locks.renewal, self.locks.policy):
            with HostLock(self.locks.maddy, self.locks.policy):
                self.require_quiet()
                yield
