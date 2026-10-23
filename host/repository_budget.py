"""Account for the whole R2 bucket before repository writes and temporary repacking."""

from typing import Any, Protocol

from host.r2 import R2Options, r2_transfer
from shared.errors import ServiceError


class RepositoryBudget(Protocol):
    def require(self, incoming_bytes: int) -> None: ...


class R2Budget:
    def __init__(self, configuration: dict[str, Any], owner: int = 0) -> None:
        self.options = R2Options.load(configuration)
        self.peer = r2_transfer(configuration, owner)

    def require(self, incoming_bytes: int) -> None:
        current = sum(item.size for item in self.peer.objects())
        if incoming_bytes < 0 or current + incoming_bytes > self.options.max_bucket_bytes:
            raise ServiceError("r2_storage_budget_exceeded", 503)
