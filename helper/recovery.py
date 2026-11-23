"""Closed recovery requests and versioned administrator policy, enforced by root."""

import hashlib
from pathlib import Path
from typing import Any

from helper.direct_deletion import DeletionRequest, DirectDeletion
from helper.recovery_records import RecoveryRecords
from helper.retention import RETENTION_CHOICES, RecoveryPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json
from shared.validation import address, bounded_text, request_id

FIELDS = {
    "recovery_policy": set(),
    "recovery_list": {"cursor"},
    "recovery_pages": {"page", "page_size"},
    "recovery_save_policy": {"policy", "acknowledged"},
    "recovery_delete": {
        "email",
        "confirmation",
        "acknowledged",
        "retention",
        "policy_revision",
        "metadata",
        "permanent",
    },
}


def validate_recovery(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload["operation"]
    if operation not in FIELDS or set(payload) != (
        {"operation", "protocol", "request_id"} | FIELDS[operation]
    ):
        raise ServiceError("invalid_helper_fields", 400)
    request_id(payload["request_id"])
    if operation == "recovery_pages" and (
        type(payload["page"]) is not int
        or payload["page"] < 1
        or type(payload["page_size"]) is not int
        or payload["page_size"] not in {5, 10, 20, 50}
    ):
        raise ServiceError("invalid_pagination", 400)
    if operation == "recovery_list" and payload["cursor"]:
        request_id(payload["cursor"])
    if operation == "recovery_delete":
        if payload["acknowledged"] is not True:
            raise ServiceError("deletion_confirmation_required", 400)
        address(payload["confirmation"])
    if operation == "recovery_delete":
        if (
            payload["email"] != address(payload["email"])
            or payload["confirmation"] != payload["email"]
        ):
            raise ServiceError("deletion_confirmation_required", 400)
        if (
            payload["retention"] not in RETENTION_CHOICES
            or type(payload["permanent"]) is not bool
            or type(payload["policy_revision"]) is not int
        ):
            raise ServiceError("invalid_retention", 400)
        if payload["retention"] == "none" and not payload["permanent"]:
            raise ServiceError("invalid_retention", 400)
        meta = payload["metadata"]
        if not isinstance(meta, dict) or set(meta) != {"display_name", "notes", "created"}:
            raise ServiceError("invalid_mailbox_metadata", 400)
        bounded_text(meta["display_name"], 256)
        bounded_text(meta["notes"], 2048)
        if type(meta["created"]) not in {int, float} or meta["created"] <= 0:
            raise ServiceError("invalid_mailbox_metadata", 400)
    if operation == "recovery_save_policy":
        validate_policy(payload["policy"])
        if payload["acknowledged"] is not True:
            raise ServiceError("policy_acknowledgment_required", 400)
    return {
        key: value
        for key, value in payload.items()
        if key not in {"operation", "protocol", "request_id"}
    }


def validate_policy(policy: Any) -> RecoveryPolicy:
    if (
        not isinstance(policy, dict)
        or set(policy)
        != {
            "schema_version",
            "revision",
            "enabled",
            "permanent_deletion_enabled",
            "default_retention",
            "protected",
        }
        or not isinstance(policy["protected"], dict)
    ):
        raise ServiceError("recovery_policy_invalid", 400)
    parsed = RecoveryPolicy.parse(policy)
    if len(policy["protected"]) > 16:
        raise ServiceError("recovery_policy_invalid", 400)
    for email, reason in policy["protected"].items():
        if not isinstance(email, str) or email != address(email):
            raise ServiceError("recovery_policy_invalid", 400)
        if not isinstance(reason, str) or not reason:
            raise ServiceError("recovery_policy_invalid", 400)
        bounded_text(reason, 128)
    return parsed


class RecoveryControl:
    def __init__(
        self, directory: Path, records: RecoveryRecords, owner: int = 0,
        policy_owner: int | None = None, deletion: DirectDeletion | None = None,
    ) -> None:
        # The root workers write the proof, key and recovery points (owner); the policy is edited
        # through the dashboard, so it belongs to whoever owns the shared state.
        self.directory, self.records, self.owner = directory, records, owner
        self.deletion = deletion
        self.policy_owner = owner if policy_owner is None else policy_owner
        self.policy_path = directory / "recovery-policy.json"
        self.availability: dict[str, tuple[tuple[Any, ...], bool]] = {}

    def policy(self) -> dict[str, Any]:
        policy = protected_json(self.policy_path, self.policy_owner)
        validate_policy(policy)
        return policy

    def _available(self, row: dict[str, Any]) -> bool:
        if not row["snapshot"] or row["state"] in {"capturing", "removed", "removing"}:
            return False
        try:
            from host.mailbox_archive import MailboxArchives

            root = self.directory / "recovery-points"
            key = self.directory / "recovery.key"
            paths = [root / (row["snapshot"] + suffix) for suffix in (".enc", ".json")]
            fingerprint = (
                hashlib.sha256(protected_bytes(key, self.owner)).hexdigest(),
                *(
                    (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in paths
                ),
            )
            previous = self.availability.get(row["id"])
            if previous and previous[0] == fingerprint:
                return previous[1]
            valid = MailboxArchives(root, key, self.owner).available(row["snapshot"], row["email"])
            self.availability[row["id"]] = (fingerprint, valid)
            return valid
        except (OSError, ValueError, ServiceError):
            return False

    def handle(
        self, operation: str, identifier: str, values: dict[str, Any], now: int
    ) -> dict[str, Any]:
        if operation == "recovery_policy":
            return {"policy": self.policy()}
        if operation in {"recovery_list", "recovery_pages"}:
            return self._listing(operation, values)
        if operation == "recovery_save_policy":
            return self._save_policy(identifier, values, now)
        return self._delete(identifier, values, now)

    def _listing(self, operation: str, values: dict[str, Any]) -> dict[str, Any]:
        rows = self.records.listing()
        total = len(rows)
        size = values.get("page_size", 10)
        page = min(values.get("page", 1), max(1, (total + size - 1) // size))
        if operation == "recovery_pages":
            rows = rows[(page - 1) * size :]
        cursor = values.get("cursor", "")
        if cursor:
            positions = [index for index, row in enumerate(rows) if row["id"] == cursor]
            if not positions:
                raise ServiceError("recovery_page_changed", 409)
            rows = rows[positions[0] + 1 :]
        return {
            "mailboxes": [self._public(row) for row in rows[:size]],
            "next_cursor": rows[size - 1]["id"] if len(rows) > size else None,
            "total": total,
            "page": page,
            "page_size": size,
        }

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        item = {key: value for key, value in row.items() if key not in {"notes", "snapshot"}}
        item["recovery_available"] = self._available(row)
        item["phase"] = row["state"]
        if row["state"] in {"capturing", "deleting", "restoring", "removing"}:
            item.update(state="reconciliation_required", error="operation_interrupted")
        return item

    def _save_policy(self, identifier: str, values: dict[str, Any], now: int) -> dict[str, Any]:
        policy = self.policy()
        proposed = values["policy"]
        if proposed["revision"] != policy["revision"]:
            raise ServiceError("recovery_policy_changed", 409)
        proposed = {**proposed, "revision": policy["revision"] + 1}
        self._audit(identifier, now, "intent_recorded")
        atomic_json(self.policy_path, proposed)
        self._audit(identifier, now, "complete")
        return {"policy": proposed}

    def _audit(self, identifier: str, now: int, outcome: str) -> None:
        with self.records.transaction() as connection:
            connection.execute(
                "INSERT INTO recovery_audit(created,action,request_id,email,outcome) "
                "VALUES(?,?,?,?,?)",
                (now, "recovery_save_policy", identifier, "", outcome),
            )

    def _delete(self, identifier: str, values: dict[str, Any], now: int) -> dict[str, Any]:
        policy = self.policy()
        if self.deletion is None:
            raise ServiceError("recovery_unavailable", 503)
        if values["policy_revision"] != policy["revision"]:
            raise ServiceError("recovery_policy_changed", 409)
        if not policy["enabled"]:
            raise ServiceError("deletion_disabled", 403)
        if values["email"] in policy["protected"]:
            raise ServiceError("protected_recipient", 403)
        if values["permanent"] and not policy["permanent_deletion_enabled"]:
            raise ServiceError("permanent_deletion_disabled", 403)
        request = DeletionRequest(
            identifier, values["email"], values["retention"], values["permanent"],
            values["metadata"],
        )
        return self.deletion.delete(request, RecoveryPolicy.parse(policy), now)
