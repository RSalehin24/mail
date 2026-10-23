"""Crash-persistent stopped-consumer intent around the isolated offline utility."""

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from host.certificates import Generations
from host.coordination import Coordination
from host.locks import HostLock
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


class RecoveryConsumer(Protocol):
    def pause_runtime(self) -> None: ...
    def stop(self) -> None: ...
    def snapshot_authentication(self) -> None: ...
    def offline_utility(self, name: str) -> None: ...
    def discard_recovery(self, name: str) -> None: ...
    def verify_recovery(self, name: str) -> None: ...
    def allow_runtime(self) -> None: ...
    def verify_running(self) -> None: ...


class Recovery:
    def __init__(
        self, coordination: Coordination, dependencies: tuple[Generations, RecoveryConsumer, Path]
    ) -> None:
        self.coordination = coordination
        self.generations, self.consumer, self.outcome = dependencies

    def run(self) -> None:
        with HostLock(self.outcome.with_suffix(".lock"), self.coordination.locks.policy):
            self._run()

    def _run(self) -> None:
        name = "mail-admin-recovery-" + str(uuid.uuid4())
        with self.coordination.consistent():
            self._require_migrated()
            if self.outcome.exists():
                self.consumer.discard_recovery(self._record()["utility_name"])
            self.consumer.pause_runtime()
            self.generations.maintenance("mail-admin", "offline_recovery")
            self.consumer.stop()
            self.consumer.snapshot_authentication()
            atomic_json(
                self.outcome,
                {
                    "state": "started",
                    "consumer_stopped": True,
                    "utility_name": name,
                },
            )
        # The utility has no helper/network access; host locks are never inherited.
        self.consumer.offline_utility(name)
        atomic_json(
            self.outcome,
            {
                "state": "completed",
                "consumer_stopped": True,
                "utility_name": name,
                "resume": "explicit_operator_action_required",
            },
        )

    def _record(self) -> dict[str, Any]:
        record = protected_json(self.outcome, self.generations.policy.owner)
        state = record.get("state")
        if (
            not isinstance(state, str)
            or state not in {"started", "completed", "resume_authorized", "resumed"}
            or not isinstance(record.get("utility_name"), str)
        ):
            raise ServiceError("invalid_recovery_outcome", 503)
        return record

    def resume(self, activate: Callable[[], None]) -> None:
        with HostLock(self.outcome.with_suffix(".lock"), self.coordination.locks.policy):
            self._authorize_resume()
            # The recovery lease stays held; renewal and Maddy locks are released.
            activate()
            self._confirm_activation()

    def _authorize_resume(self) -> None:
        with self.coordination.consistent():
            self._require_migrated()
            record = self._record()
            self.consumer.verify_recovery(record["utility_name"])
            self.consumer.stop()
            record["state"] = "resume_authorized"
            atomic_json(self.outcome, record)
            self.consumer.allow_runtime()
            self.generations.register("mail-admin")

    def _require_migrated(self) -> None:
        if self.generations.read().get("coordination_migrated") is not True:
            raise ServiceError("matched_renewal_cutover_required", 409)

    def _confirm_activation(self) -> None:
        with self.coordination.consistent():
            record = self._record()
            if record.get("state") != "resume_authorized":
                raise ServiceError("recovery_resume_not_authorized", 409)
            self.consumer.verify_running()
            record.update({"state": "resumed", "consumer_stopped": False})
            atomic_json(self.outcome, record)
