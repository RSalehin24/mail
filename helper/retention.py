"""Versioned root-enforced retention rules for deleted mailbox recovery points."""

from dataclasses import dataclass
from typing import Any

from shared.errors import ServiceError

RETENTION_CHOICES = ("7_days", "30_days", "forever", "none")


@dataclass(frozen=True)
class RecoveryPolicy:
    revision: int
    enabled: bool
    permanent_deletion_enabled: bool
    default_retention: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> "RecoveryPolicy":
        if (
            type(value.get("schema_version")) is not int
            or value.get("schema_version") != 1
            or type(value.get("revision")) is not int
            or value["revision"] < 1
            or type(value.get("enabled")) is not bool
            or type(value.get("permanent_deletion_enabled")) is not bool
            or (
                value.get("default_retention") == "none"
                and not value.get("permanent_deletion_enabled")
            )
            or value.get("default_retention") not in RETENTION_CHOICES
        ):
            raise ServiceError("recovery_policy_invalid", 503)
        return cls(
            value["revision"],
            value["enabled"],
            value["permanent_deletion_enabled"],
            value["default_retention"],
        )

    def deadline(self, retention: str, deleted_at: int) -> int | None:
        if not self.enabled:
            raise ServiceError("deletion_disabled", 403)
        if retention not in RETENTION_CHOICES:
            raise ServiceError("invalid_retention", 400)
        if type(deleted_at) is not int or deleted_at < 0:
            raise ServiceError("invalid_deletion_time", 400)
        if retention == "none":
            if not self.permanent_deletion_enabled:
                raise ServiceError("permanent_deletion_disabled", 403)
            return deleted_at
        if retention == "forever":
            return None
        return deleted_at + (7 if retention == "7_days" else 30) * 86400


def recovery_expired(deadline: int | None, now: int) -> bool:
    """An unlimited recovery point never becomes eligible for automatic pruning."""
    return deadline is not None and now >= deadline
