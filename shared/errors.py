"""Safe boundary errors; raw subprocess and authentication errors stay private."""

from dataclasses import dataclass


@dataclass
class ServiceError(Exception):
    code: str
    status: int = 400
    retry_after: int | None = None

    def __str__(self) -> str:
        return self.code
