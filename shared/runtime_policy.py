"""Root maintenance intent is visible through the existing stable secrets-directory bind."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import atomic_group_json, protected_json

RUNTIME_POLICY_NAME = "runtime-policy.json"


@dataclass(frozen=True)
class RuntimePolicy:
    marker: Path = Path("/run/mail-admin-installation.json")
    path: Path = Path("/run/secrets/mail-admin") / RUNTIME_POLICY_NAME
    owner: int = 0

    def read(self) -> dict[str, Any]:
        marker = protected_json(self.marker, self.owner)
        record = protected_json(self.path, self.owner)
        identity = marker.get("installation_id")
        if (
            not isinstance(identity, str)
            or not identity
            or record.get("installation_id") != identity
        ):
            raise ServiceError("runtime_policy_identity_mismatch", 503)
        intent = record.get("intent")
        if not isinstance(intent, str) or intent not in {"expected_running", "maintenance"}:
            raise ServiceError("invalid_runtime_intent", 503)
        return record

    def require_running(self) -> None:
        if self.read()["intent"] != "expected_running":
            raise ServiceError("operator_maintenance", 503)

    def pause(self) -> None:
        record = self.read()
        record["intent"] = "maintenance"
        atomic_group_json(self.path, record, self.path.stat().st_gid)

    def resume(self) -> None:
        record = self.read()
        record["intent"] = "expected_running"
        atomic_group_json(self.path, record, self.path.stat().st_gid)
