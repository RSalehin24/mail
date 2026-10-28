"""Closed recovery requests and versioned administrator policy, enforced by root."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from helper.recovery_records import RecoveryRecords
from helper.retention import RETENTION_CHOICES, RecoveryPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json
from shared.validation import address, bounded_text, request_id

QUALIFIED_FILES = frozenset(
    {
        "helper/recovery.py",
        "helper/recovery_records.py",
        "helper/retention.py",
        "helper/service.py",
        "host/mailbox_recovery.py",
        "host/mailbox_store.py",
        "host/mailbox_archive.py",
        "host/mailbox_transaction.py",
        "helper/backup_control.py",
        "host/backup_control.py",
        "host/backup_merge.py",
        "host/backup_operations.py",
        "host/repository_backup.py",
        "host/daily_retention.py",
        "host/restic_repository.py",
        "host/initial_cleanup.py",
    }
)


def require_qualified(directory: Path, owner: int = 0) -> None:
    proof = protected_json(directory / "recovery-qualified.json", owner)
    files = proof.get("files")
    if (
        proof.get("schema_version") != 1
        or proof.get("qualified") is not True
        or not isinstance(files, dict)
        or not QUALIFIED_FILES <= files.keys()
    ):
        raise ServiceError("recovery_not_qualified", 403)
    root = Path(__file__).resolve().parents[1]
    for name, expected in files.items():
        if (
            not isinstance(name, str)
            or name.startswith("/")
            or ".." in Path(name).parts
            or hashlib.sha256((root / name).read_bytes()).hexdigest() != expected
        ):
            raise ServiceError("recovery_qualification_changed", 403)


FIELDS = {
    "recovery_policy": set(),
    "recovery_list": {"cursor"},
    "recovery_pages": {"page", "page_size"},
    "recovery_outcome": {"job_id"},
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
    "recovery_restore": {"record_id", "confirmation", "acknowledged", "password_hash"},
    "recovery_reconcile": {"record_id", "confirmation", "acknowledged"},
    "recovery_remove": {"record_id", "confirmation", "acknowledged"},
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
    if operation in {
        "recovery_delete",
        "recovery_restore",
        "recovery_remove",
        "recovery_reconcile",
    }:
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
    if operation in {"recovery_restore", "recovery_remove", "recovery_reconcile"}:
        request_id(payload["record_id"])
    if operation == "recovery_restore" and (
        not isinstance(payload["password_hash"], str)
        or not re.fullmatch(
            r"argon2:3:(?:19456|32768|65536):1:[A-Za-z0-9+/]{22}==:[A-Za-z0-9+/]{86}==",
            payload["password_hash"],
        )
    ):
        raise ServiceError("invalid_password_hash", 400)
    if operation == "recovery_outcome":
        request_id(payload["job_id"])
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
    def __init__(self, directory: Path, records: RecoveryRecords, owner: int = 0) -> None:
        self.directory, self.records, self.owner = directory, records, owner
        self.policy_path = directory / "recovery-policy.json"
        self.availability: dict[str, tuple[tuple[Any, ...], bool]] = {}

    def policy(self) -> dict[str, Any]:
        policy = protected_json(self.policy_path, self.owner)
        validate_policy(policy)
        if policy["enabled"]:
            require_qualified(self.directory, self.owner)
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
            rows = self.records.listing()
            with self.records.transaction() as connection:
                orphaned = connection.execute(
                    "SELECT * FROM recovery_jobs WHERE kind='recovery_delete' "
                    "AND state IN ('queued','running','reconciliation_required') "
                    "AND record_id NOT IN (SELECT id FROM recovery_records) "
                    "ORDER BY updated_at DESC,id",
                ).fetchall()
            for job in orphaned:
                payload = json.loads(job["payload"])
                rows.append(
                    {
                        "id": job["record_id"],
                        "email": job["email"],
                        "state": job["state"],
                        "snapshot": None,
                        "display_name": payload["metadata"]["display_name"],
                        "retention": payload["retention"],
                        "deadline": None,
                        "deleted_at": job["updated_at"],
                        "deletion_pending": True,
                        "updated_at": job["updated_at"],
                    }
                )
            rows.sort(key=lambda row: (row["deleted_at"], row["id"]), reverse=True)
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
            public = []
            for row in rows[:size]:
                item = {
                    key: value for key, value in row.items() if key not in {"notes", "snapshot"}
                }
                item["recovery_available"] = self._available(row)
                checkpoint = self.records.checkpoint_for(row["id"])
                item["phase"] = checkpoint["phase"] if checkpoint else row["state"]
                with self.records.transaction() as connection:
                    job = connection.execute(
                        "SELECT state,error FROM recovery_jobs "
                        "WHERE record_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                if job is not None and job["state"] in {"queued", "running"}:
                    item["state"] = job["state"]
                elif (job is not None and job["state"] == "reconciliation_required") or row[
                    "state"
                ] in {"capturing", "deleting", "restoring", "removing"}:
                    item.update(
                        state="reconciliation_required",
                        error=job["error"] if job else "operation_interrupted",
                    )
                public.append(item)
            return {
                "mailboxes": public,
                "next_cursor": rows[size - 1]["id"] if len(rows) > size else None,
                "total": total,
                "page": page,
                "page_size": size,
            }
        if operation == "recovery_outcome":
            result = self.records.job(values["job_id"])
            original = self.records.original_job(values["job_id"])
            checkpoint = self.records.checkpoint_for(original["record_id"])
            return {
                **result,
                "phase": checkpoint["phase"] if checkpoint else result["state"],
                "operation": original["kind"],
                "email": original["email"],
                "selection": {
                    key: original["payload"][key]
                    for key in ("retention", "permanent", "policy_revision")
                    if key in original["payload"]
                },
            }
        if operation not in {"recovery_save_policy"}:
            try:
                self.records.original_job(identifier)
            except ServiceError as error:
                if error.code != "operation_not_found":
                    raise
            else:
                record_id = identifier if operation == "recovery_delete" else values["record_id"]
                email = (
                    values["email"] if operation == "recovery_delete" else values["confirmation"]
                )
                return self.records.enqueue(identifier, operation, record_id, email, values, now)
        policy = self.policy()
        if operation == "recovery_save_policy":
            proposed = values["policy"]
            if proposed["revision"] != policy["revision"]:
                raise ServiceError("recovery_policy_changed", 409)
            if proposed["enabled"]:
                require_qualified(self.directory, self.owner)
            proposed = {**proposed, "revision": policy["revision"] + 1}
            with self.records.transaction() as connection:
                connection.execute(
                    "INSERT INTO recovery_audit(created,action,request_id,email,outcome) "
                    "VALUES(?,?,?,?,?)",
                    (now, operation, identifier, "", "intent_recorded"),
                )
            atomic_json(self.policy_path, proposed)
            with self.records.transaction() as connection:
                connection.execute(
                    "INSERT INTO recovery_audit "
                    "(created,action,request_id,email,outcome) VALUES(?,?,?,?,?)",
                    (now, operation, identifier, "", "complete"),
                )
            return {"policy": proposed}
        if operation != "recovery_reconcile" and any(
            row["state"]
            in {"capturing", "deleting", "restoring", "removing", "reconciliation_required"}
            for row in self.records.listing()
        ):
            raise ServiceError("reconciliation_required", 409)
        if operation == "recovery_delete":
            if values["policy_revision"] != policy["revision"]:
                raise ServiceError("recovery_policy_changed", 409)
            if not policy["enabled"]:
                raise ServiceError("deletion_disabled", 403)
            if values["email"] in policy["protected"]:
                raise ServiceError("protected_recipient", 403)
            if values["permanent"] and not policy["permanent_deletion_enabled"]:
                raise ServiceError("permanent_deletion_disabled", 403)
            email, record_id = values["email"], identifier
        else:
            try:
                record = self.records.get(values["record_id"])
            except ServiceError as error:
                if error.code != "deleted_mailbox_not_found" or operation != "recovery_reconcile":
                    raise
                original = self.records.original_job(values["record_id"])
                if original["kind"] != "recovery_delete":
                    raise ServiceError("invalid_recovery_operation", 409) from error
                record = {
                    "email": original["email"],
                    "id": original["record_id"],
                    "state": "reconciliation_required",
                }
            email, record_id = record["email"], record["id"]
            if values["confirmation"] != email:
                raise ServiceError("deletion_confirmation_required", 400)
            if operation == "recovery_remove" and not policy["permanent_deletion_enabled"]:
                raise ServiceError("permanent_deletion_disabled", 403)
            if operation != "recovery_reconcile" and record["state"] not in (
                {"deleted", "restored", "cancelled"}
                if operation == "recovery_remove"
                else {"deleted", "cancelled"}
            ):
                raise ServiceError("recovery_state_conflict", 409)
            if operation == "recovery_restore" and (
                record["deadline"] is not None and now >= record["deadline"]
            ):
                raise ServiceError("recovery_expired", 409)
        return self.records.enqueue(identifier, operation, record_id, email, values, now)
