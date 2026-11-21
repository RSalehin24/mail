"""Crash-persistent record of which Docker consumers a maintenance step stopped."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import atomic_json, protected_json

CONSUMERS = ("maddy", "mail-admin")


class ConsumerIntents:
    def __init__(self, path: Path, owner: int = 0, clock: Callable[[], float] = time.time) -> None:
        self.path, self.owner, self.clock = path, owner, clock

    def read(self) -> dict[str, Any]:
        record = protected_json(self.path, self.owner) if self.path.exists() else {}
        consumers = record.setdefault("consumers", {})
        for name in CONSUMERS:
            consumers.setdefault(name, {"intent": "expected_running"})
        return record

    def write(self, record: dict[str, Any]) -> None:
        record["observed_at"] = self.clock()
        atomic_json(self.path, record)

    def register(self, consumer: str) -> None:
        state = self._known(consumer)
        state["consumers"][consumer] = {"intent": "expected_running"}
        self.write(state)

    def maintenance(self, consumer: str, reason: str, served_sha256: str | None = None) -> None:
        """Mark a consumer stopped on purpose; the served fingerprint is what resuming must see."""
        state = self._known(consumer)
        record: dict[str, Any] = {"intent": "maintenance", "reason": reason}
        if served_sha256 is not None:
            record["served_sha256"] = served_sha256
        state["consumers"][consumer] = record
        self.write(state)

    def _known(self, consumer: str) -> dict[str, Any]:
        if consumer not in CONSUMERS:
            raise ServiceError("unknown_consumer", 400)
        return self.read()
