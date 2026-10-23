"""One measured resource policy for root one-off image and ACME work."""

import math
from dataclasses import dataclass
from typing import Any

from shared.errors import ServiceError

TEMPORARY_PATH = "/tmp"  # noqa: S108 -- a bounded private container tmpfs, not the host /tmp.


@dataclass(frozen=True)
class ContainerPolicy:
    memory: int
    temporary: int
    cpus: float

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> "ContainerPolicy":
        values = configuration.get("transient_resources")
        if not isinstance(values, dict):
            raise ServiceError("measured_transient_resources_required", 503)
        memory, temporary = values.get("memory_limit_bytes"), values.get("tmpfs_limit_bytes")
        cpus = values.get("cpu_limit")
        if type(memory) is not int or type(temporary) is not int:
            raise ServiceError("measured_transient_resources_required", 503)
        if not isinstance(cpus, (int, float)) or isinstance(cpus, bool):
            raise ServiceError("measured_transient_resources_required", 503)
        if not 0 < temporary < memory or not math.isfinite(cpus) or cpus <= 0:
            raise ServiceError("measured_transient_resources_required", 503)
        return cls(memory, temporary, float(cpus))

    def arguments(self, temporary_paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(temporary_paths) * self.temporary >= self.memory:
            raise ServiceError("transient_temporary_budget_exceeds_memory", 503)
        arguments: tuple[str, ...] = (
            "--memory", str(self.memory), "--memory-swap", str(self.memory),
            "--cpus", str(self.cpus), "--pids-limit", "128",
            "--log-driver", "local", "--log-opt", "max-size=5m", "--log-opt", "max-file=2",
        )
        for path in temporary_paths:
            arguments += ("--tmpfs", f"{path}:size={self.temporary},mode=1777")
        return arguments
