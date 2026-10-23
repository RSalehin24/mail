"""Bind operational approval and gate reports to the qualified artifact set and target."""

import hashlib
import json
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json


@dataclass(frozen=True)
class RolloutPaths:
    directory: Path = Path("/var/lib/mail-admin-rollout")
    approval: Path = Path("/etc/mail-admin/maintenance-approval.json")


def qualified_identity(deployment: dict[str, Any]) -> str:
    identities = [
        deployment.get(key)
        for key in ("artifact_set_sha256", "specification_sha256", "qualification_suite_sha256")
    ]
    if not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in identities
    ):
        raise ServiceError("qualified_artifact_set_required", 409)
    return str(identities[0])


def rollout_directory(deployment: dict[str, Any]) -> Path:
    return RolloutPaths().directory / qualified_identity(deployment)


def trusted_target(deployment: dict[str, Any]) -> dict[str, str]:
    inputs = deployment["inputs"]
    target = {key: inputs.get(key) for key in ("target_instance_id", "target_ssh_host_key_sha256")}
    if not all(isinstance(value, str) and value for value in target.values()):
        raise ServiceError("trusted_target_required", 409)
    if not re.fullmatch(r"[0-9a-f]{64}", str(target["target_ssh_host_key_sha256"])):
        raise ServiceError("trusted_target_required", 409)
    return {key: str(value) for key, value in target.items()}


def rollout_operation(deployment: dict[str, Any]) -> HostLock:
    directory = rollout_directory(deployment)
    return HostLock(
        directory.parent / "operation.lock", LockPolicy(deployment["inputs"]["lock_wait_seconds"])
    )


def require_private_directory(path: Path, owner: int) -> None:
    metadata = path.lstat()
    if (
        path.resolve() != path.absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ServiceError("untrusted_maintenance_directory", 503)


class RolloutState:
    def __init__(self, deployment: dict[str, Any], policy: tuple[Path, int]) -> None:
        self.deployment = deployment
        self.directory, self.owner = policy
        require_private_directory(self.directory, self.owner)
        self.path = self.directory / "active-release.json"

    def _binding(self) -> dict[str, Any]:
        return {
            "artifact_set_sha256": qualified_identity(self.deployment),
            "specification_sha256": self.deployment["specification_sha256"],
            "qualification_suite_sha256": self.deployment["qualification_suite_sha256"],
            "target": trusted_target(self.deployment),
        }

    def require_selected(self) -> None:
        if protected_json(self.path, self.owner) != self._binding():
            raise ServiceError("another_rollout_selected", 409)

    def select(self) -> None:
        for name in ("legacy.json", "cutover.json", "rollback-set"):
            if (self.directory / name).exists() or (self.directory / name).is_symlink():
                raise ServiceError("unnamespaced_rollout_requires_reconciliation", 409)
        binding = self._binding()
        if self.path.is_symlink():
            raise ServiceError("untrusted_active_rollout", 503)
        if self.path.exists():
            previous = protected_json(self.path, self.owner)
            if previous == binding:
                return
            if previous.get("target") != binding["target"]:
                raise ServiceError("rollout_target_changed", 409)
            self._require_complete(previous)
        else:
            self._require_no_previous_state()
        atomic_json(self.path, binding)

    def _require_no_previous_state(self) -> None:
        names = ("legacy.json", "cutover.json", "baseline-manifest.json", "rollback-set")
        for directory in self.directory.iterdir():
            if re.fullmatch(r"[0-9a-f]{64}", directory.name) and any(
                (directory / name).exists() or (directory / name).is_symlink()
                for name in names
            ):
                raise ServiceError("active_rollout_missing_requires_reconciliation", 409)

    def _require_complete(self, previous: dict[str, Any]) -> None:
        identity = qualified_identity(previous)
        directory = self.directory / identity
        require_private_directory(directory, self.owner)
        legacy = protected_json(directory / "legacy.json", self.owner)
        cutover = protected_json(directory / "cutover.json", self.owner)
        if legacy.get("manual_completion") not in (None, "reconciled") or cutover.get(
            "phase"
        ) not in {"complete", "rolled_back"}:
            raise ServiceError("previous_rollout_unresolved", 409)
        if cutover["phase"] == "complete":
            deployment = {**previous, "inputs": previous["target"]}
            RolloutGuard(deployment, (
                RolloutPaths(directory=directory), self.owner, time.time,
            )).checkpoint("G08")


def timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError) as error:
        raise ServiceError("invalid_rollout_timestamp", 409) from error


class RolloutGuard:
    def __init__(
        self, deployment: dict[str, Any], policy: tuple[RolloutPaths, int, Callable[[], float]]
    ) -> None:
        self.deployment = deployment
        self.paths, self.owner, self.clock = policy

    def _identity(self) -> str:
        return qualified_identity(self.deployment)

    def _target(self) -> dict[str, str]:
        return trusted_target(self.deployment)

    def approval(self, phase: str) -> dict[str, Any]:
        record = protected_json(self.paths.approval, self.owner)
        approved, expires = (
            timestamp(record.get("approved_at_utc")),
            timestamp(record.get("expires_at_utc")),
        )
        valid = (
            record.get("artifact_set_sha256") == self._identity()
            and record.get("target") == self._target()
            and phase in record.get("allowed_phases", [])
            and isinstance(record.get("approved_by"), str)
            and bool(record["approved_by"].strip())
            and approved <= self.clock() < expires <= approved + 86400
        )
        if not valid:
            raise ServiceError("operator_approval_required", 403)
        return record

    def checkpoint(self, phase: str) -> dict[str, Any]:
        contracts = (
            Path(__file__).resolve().parents[1]
            / "docs/mail-admin-implementation/requirements/contracts.json"
        )
        gates = {gate["id"]: gate for gate in json.loads(contracts.read_text())["gates"]}
        if phase not in gates:
            raise ServiceError("unknown_checkpoint", 409)
        records = protected_json(self.paths.directory / "checkpoints.json", self.owner)
        report = self._report(records.get(phase, {}), gates[phase])
        for dependency in gates[phase]["depends_on"]:
            previous = self.checkpoint(dependency)
            if timestamp(previous["completed_at_utc"]) > timestamp(report["started_at_utc"]):
                raise ServiceError("checkpoint_order_invalid", 409)
        return report

    def _report(self, reference: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        relative = reference.get("report", "")
        if not isinstance(relative, str) or not re.fullmatch(
            r"reports/[a-zA-Z0-9_.-]+\.json", relative
        ):
            raise ServiceError("required_checkpoint_missing", 409)
        path = self.paths.directory / relative
        if not path.resolve().is_relative_to(self.paths.directory.resolve()):
            raise ServiceError("untrusted_checkpoint_path", 409)
        content = protected_bytes(path, self.owner)
        report = protected_json(path, self.owner)
        if hashlib.sha256(content).hexdigest() != reference.get("report_sha256"):
            raise ServiceError("checkpoint_digest_mismatch", 409)
        self._binding(report, gate)
        return report

    def _binding(self, report: dict[str, Any], gate: dict[str, Any]) -> None:
        expected = [{"id": case["id"], "result": "pass"} for case in gate["cases"]]
        valid = (
            type(report.get("schema_version")) is int
            and report["schema_version"] == 1
            and report.get("test_id") == gate["id"]
            and report.get("artifact_set_sha256") == self._identity()
            and report.get("specification_sha256") == self.deployment.get("specification_sha256")
            and report.get("runner_revision") == self.deployment.get("qualification_suite_sha256")
            and report.get("result") == "pass"
            and report.get("cases") == expected
            and report.get("environment") == gate["environment"]
            and timestamp(report.get("started_at_utc"))
            <= timestamp(report.get("completed_at_utc"))
            <= self.clock()
        )
        if not valid or (gate["id"] != "G01" and report.get("observed_target") != self._target()):
            raise ServiceError("checkpoint_binding_mismatch", 409)


def operational_guard(deployment: dict[str, Any]) -> RolloutGuard:
    paths = RolloutPaths(directory=rollout_directory(deployment))
    return RolloutGuard(deployment, (paths, 0, time.time))
