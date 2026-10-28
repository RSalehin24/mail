"""Serialized mailbox transactions and durable, conservative partial outcomes."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from helper.backup_control import BackupControl
from helper.journal import Journal
from helper.maddy import MaddyAccess, cli_reported_error, cli_succeeded, provisioning_warning
from helper.protocol import HelperRequest
from helper.recovery import RecoveryControl
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.validation import HELPER_PROTOCOL


class DeleteChecks(Protocol):
    def capability(self) -> dict[str, Any]: ...
    def check(self, request: HelperRequest, journal: Journal) -> None: ...


@dataclass(frozen=True)
class HelperDependencies:
    maddy: MaddyAccess
    journal: Journal
    deletion: DeleteChecks
    lock: Path
    lock_policy: LockPolicy
    clock: Callable[[], float]
    status: Callable[[], dict[str, Any]]
    recovery: RecoveryControl | None = None
    backups: BackupControl | None = None


class HostHelper:
    def __init__(self, dependencies: HelperDependencies) -> None:
        self.dependencies = dependencies
        self.maddy, self.journal = dependencies.maddy, dependencies.journal

    def handle(self, request: HelperRequest) -> dict[str, Any]:
        if request.operation.startswith("backup_"):
            if self.dependencies.backups is None or request.recovery is None:
                raise ServiceError("backup_control_unavailable", 503)
            with HostLock(self.dependencies.lock, self.dependencies.lock_policy):
                return {
                    "protocol": HELPER_PROTOCOL,
                    **self.dependencies.backups.handle(
                        request.operation,
                        request.request_id,
                        request.recovery,
                        self.dependencies.clock(),
                    ),
                }
        if request.operation.startswith("recovery_"):
            if self.dependencies.recovery is None or request.recovery is None:
                raise ServiceError("recovery_unavailable", 503)
            with HostLock(self.dependencies.lock, self.dependencies.lock_policy):
                return {
                    "protocol": HELPER_PROTOCOL,
                    **self.dependencies.recovery.handle(
                        request.operation,
                        request.request_id,
                        request.recovery,
                        int(self.dependencies.clock()),
                    ),
                }
        if request.operation == "outcome":
            return {"protocol": HELPER_PROTOCOL, "outcome": self.journal.lookup(request.request_id)}
        if request.operation == "telemetry":
            return {"protocol": HELPER_PROTOCOL, "summary": self.dependencies.status()}
        if request.operation == "status":
            return {
                "protocol": HELPER_PROTOCOL,
                "summary": self.dependencies.status(),
                "maddy": self._status(),
                "unresolved": self.journal.unresolved(),
            }
        if request.operation == "delete":
            self.dependencies.deletion.capability()
        with HostLock(self.dependencies.lock, self.dependencies.lock_policy):
            return self._locked(request)

    def _status(self) -> dict[str, Any]:
        try:
            with HostLock(self.dependencies.lock, self.dependencies.lock_policy):
                self.maddy.validate()
                inventory = self.maddy.inventories()
                return {
                    "state": "available",
                    "observed_at": self.dependencies.clock(),
                    "credential_count": len(inventory["credentials"]),
                    "storage_count": len(inventory["storage"]),
                }
        except (OSError, ServiceError) as error:
            return {
                "state": "unknown",
                "observed_at": self.dependencies.clock(),
                "reason": error.code
                if isinstance(error, ServiceError)
                else "observation_unavailable",
            }

    def _locked(self, request: HelperRequest) -> dict[str, Any]:
        if self.dependencies.recovery is not None and request.operation != "inventory":
            if any(
                row["state"]
                in {"capturing", "deleting", "restoring", "removing", "reconciliation_required"}
                for row in self.dependencies.recovery.records.listing()
            ):
                raise ServiceError("reconciliation_required", 409)
        previous = self.journal.lookup(request.request_id)
        if previous:
            return self._duplicate(request, previous)
        if self.journal.unresolved():
            raise ServiceError("reconciliation_required", 409)
        self.journal.retain(self.dependencies.clock())
        self.maddy.validate()
        inventory = self.maddy.inventories()
        self.journal.synchronize(inventory, self.dependencies.clock())
        if request.operation == "inventory":
            return {"protocol": HELPER_PROTOCOL, **inventory}
        if self.dependencies.recovery is not None and self.dependencies.recovery.records.busy():
            raise ServiceError("recovery_operation_in_progress", 409)
        self._preconditions(request, inventory)
        self.journal.begin(request, (self.dependencies.clock(), inventory))
        if request.operation == "create":
            return self._create(request)
        if request.operation == "password":
            return self._password(request)
        return self._delete(request)

    def _preconditions(self, request: HelperRequest, inventory: dict[str, list[str]]) -> None:
        credential = request.email in inventory["credentials"]
        storage = request.email in inventory["storage"]
        if request.operation == "create" and (credential or storage):
            raise ServiceError("account_conflict", 409)
        if request.operation != "create" and not (credential and storage):
            raise ServiceError(
                "account_conflict" if credential or storage else "account_not_found",
                409 if credential or storage else 404,
            )
        if request.operation == "delete":
            self.dependencies.deletion.check(request, self.journal)

    def _duplicate(self, request: HelperRequest, record: dict[str, Any]) -> dict[str, Any]:
        if record["operation"] != request.operation or record["email"] != request.email:
            raise ServiceError("request_id_conflict", 409)
        if record["phase"] or record["state"] in {"unknown", "reconciliation_required"}:
            return self._result(record)
        # Automatic replay is deliberately excluded after a response loss or host crash.
        if record["state"] == "pending":
            record = self.journal.update(request.request_id, {"state": "reconciliation_required"})
        return self._result(record)

    def _step(self, request: HelperRequest, values: tuple[str, tuple[str, ...]]) -> bool:
        label, arguments = values
        record = self.journal.update(
            request.request_id, {"phase": label, "updated": self.dependencies.clock()}
        )
        try:
            response = self.maddy.change(
                arguments,
                request.password if label in {"credentials-created", "password-changed"} else "",
            )
        except (ServiceError, OSError) as error:
            self._observe(request, {"state": "unknown"})
            raise ServiceError("command_completion_unknown", 503) from error
        steps = record["steps"] + [
            {
                "step": label,
                "exit_code": response.code,
                "error_reported": cli_reported_error(response),
                "warnings": provisioning_warning(response),
            }
        ]
        self.journal.update(
            request.request_id,
            {"steps": steps, "phase": None, "updated": self.dependencies.clock()},
        )
        return cli_succeeded(response)

    def _observe(self, request: HelperRequest, changes: dict[str, Any]) -> dict[str, Any]:
        try:
            inventory = self.maddy.inventories()
            self.journal.synchronize(inventory, self.dependencies.clock())
            changes["postconditions"] = inventory
        except (OSError, ServiceError):
            changes["postconditions"] = {"observation": "unavailable"}
        changes["updated"] = self.dependencies.clock()
        return self.journal.update(request.request_id, changes)

    def _create(self, request: HelperRequest) -> dict[str, Any]:
        if not self._step(request, ("credentials-created", ("creds", "create", request.email))):
            return self._result(self._observe(request, {"state": "reconciliation_required"}))
        stored = self._step(request, ("storage-created", ("imap-acct", "create", request.email)))
        record = self._observe(request, {})
        exists = request.email in record["postconditions"].get("storage", [])
        credentials = request.email in record["postconditions"].get("credentials", [])
        if not stored and not exists and "storage" in record["postconditions"]:
            rolled_back = self._step(
                request, ("credentials-rolled-back", ("creds", "remove", "--yes", request.email))
            )
            record = self._observe(request, {"state": "reconciliation_required"})
            absent = request.email not in record["postconditions"].get(
                "credentials", [request.email]
            )
            if rolled_back and absent:
                record = self.journal.update(request.request_id, {"state": "failed"})
        else:
            warnings = any(step["warnings"] for step in record["steps"])
            outcome = (
                "complete"
                if stored and exists and credentials and not warnings
                else "reconciliation_required"
            )
            record = self.journal.update(request.request_id, {"state": outcome})
        return self._result(record)

    def _password(self, request: HelperRequest) -> dict[str, Any]:
        success = self._step(request, ("password-changed", ("creds", "password", request.email)))
        record = self._observe(
            request, {"state": "complete" if success else "reconciliation_required"}
        )
        return self._result(record)

    def _delete(self, request: HelperRequest) -> dict[str, Any]:
        if not self._step(
            request, ("credentials-removed", ("creds", "remove", "--yes", request.email))
        ):
            return self._result(self._observe(request, {"state": "reconciliation_required"}))
        success = self._step(
            request, ("storage-removed", ("imap-acct", "remove", "--yes", request.email))
        )
        record = self._observe(request, {"state": "reconciliation_required"})
        inventory = record["postconditions"]
        absent = (
            "storage" in inventory
            and request.email not in inventory["storage"]
            and request.email not in inventory.get("credentials", [request.email])
        )
        if success and absent:
            record = self.journal.update(request.request_id, {"state": "complete"})
        return self._result(record)

    @staticmethod
    def _result(record: dict[str, Any]) -> dict[str, Any]:
        destructive = record["operation"] == "delete" and record["state"] != "complete"
        warning = (
            "Reconciliation required: some messages may already have been removed. "
            "Existing IMAP sessions may still have access."
            if destructive
            else "Existing IMAP sessions may remain active."
            if record["operation"] == "password"
            else ""
        )
        return {
            "protocol": HELPER_PROTOCOL,
            "request_id": record["id"],
            "state": record["state"],
            "warning": warning,
            "steps": record["steps"],
            "postconditions": record["postconditions"],
        }
