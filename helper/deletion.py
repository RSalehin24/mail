"""Trust only root records; browser cutoff acknowledgment is never backup evidence."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from helper.journal import Journal
from helper.protocol import HelperRequest
from shared.errors import ServiceError
from shared.files import protected_json


@dataclass(frozen=True)
class DeletionPaths:
    deployment: Path
    policy: Path
    backup: Path


class DeletionGuard:
    def __init__(self, paths: DeletionPaths, clock: Callable[[], float]) -> None:
        self.paths, self.clock = paths, clock

    def capability(self) -> dict[str, Any]:
        record = protected_json(self.paths.deployment)
        inputs = record.get('inputs')
        if not isinstance(inputs, dict) or inputs.get("permanent_deletion_enabled") is not True:
            raise ServiceError("permanent_deletion_disabled", 403)
        return record

    def check(self, request: HelperRequest, journal: Journal) -> None:
        deployment = self.capability()
        policy = protected_json(self.paths.policy)
        expected = deployment["inputs"]["protected_policy_sha256"]
        if hashlib.sha256(self.paths.policy.read_bytes()).hexdigest() != expected:
            raise ServiceError("recipient_policy_stale", 409)
        if not policy.get("reviewed_files") or policy.get("valid_until", 0) <= self.clock():
            raise ServiceError("recipient_policy_stale", 409)
        for path, digest in policy["reviewed_files"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ServiceError("recipient_policy_stale", 409)
        if request.email in policy.get("protected_recipients", []):
            raise ServiceError("protected_recipient", 403)
        self._coverage(request, (deployment, journal.incarnation(request.email)))

    def _coverage(
        self, request: HelperRequest, state: tuple[dict[str, Any], dict[str, Any]]
    ) -> None:
        deployment, incarnation = state
        manifest = protected_json(self.paths.backup)
        age_limit = deployment["inputs"]["deletion_backup_max_age_seconds"]
        cutoff = datetime.fromisoformat(manifest["cutoff"].replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ServiceError("backup_unqualified", 409)
        age = self.clock() - cutoff.timestamp()
        receipt_age = self.clock() - manifest.get("availability_observed_at", 0)
        covered = manifest.get("accounts", {}).get(request.email, {})
        valid = (
            manifest.get("verified") is True
            and manifest.get("available") is True
            and 0 <= receipt_age <= 300
            and 0 <= age <= age_limit
            and manifest.get("restore_passed") is True
            and request.cutoff == manifest["cutoff"]
            and cutoff.timestamp() >= incarnation["created"]
            and covered.get("generation") == incarnation["generation"]
            and all(covered.get(key) is True for key in ("credentials", "storage", "messages"))
        )
        if not valid:
            raise ServiceError("backup_unqualified", 409)
