"""Small, exact allowlist. No general command or path fields exist on the wire."""

from dataclasses import dataclass
from typing import Any

from shared.errors import ServiceError
from shared.validation import HELPER_PROTOCOL, address, mailbox_password, new_address, request_id


@dataclass(frozen=True)
class HelperRequest:
    operation: str
    request_id: str
    email: str = ""
    password: str = ""
    cutoff: str = ""
    recovery: dict[str, Any] | None = None

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "HelperRequest":
        operation = payload.get("operation")
        if operation == "submission_reconcile":
            if (type(payload.get("protocol")) is not int
                    or payload["protocol"] != HELPER_PROTOCOL
                    or set(payload) != {"protocol", "operation", "request_id"}):
                raise ServiceError("invalid_helper_fields", 400)
            return cls(operation, request_id(payload["request_id"]), recovery={})
        if operation == "submission_sync":
            from shared.submission import relay_configuration

            if (type(payload.get("protocol")) is not int
                    or payload["protocol"] != HELPER_PROTOCOL
                    or set(payload) != {"protocol", "operation", "request_id",
                                        "username", "credential", "revision"}):
                raise ServiceError("invalid_helper_fields", 400)
            values = {key: payload[key] for key in ("username", "credential")}
            relay_configuration(values)
            if type(payload["revision"]) is not int or payload["revision"] < 1:
                raise ServiceError("invalid_submission_revision", 400)
            values["revision"] = payload["revision"]
            return cls(operation, request_id(payload["request_id"]), recovery=values)
        if isinstance(operation, str) and operation.startswith("forwarding_"):
            from helper.forwarding import validate_forwarding

            if type(payload.get("protocol")) is not int or payload["protocol"] != HELPER_PROTOCOL:
                raise ServiceError("unsupported_helper_request", 400)
            try:
                values = validate_forwarding(payload)
            except (TypeError, AttributeError, KeyError) as error:
                raise ServiceError("invalid_helper_fields", 400) from error
            return cls(operation, request_id(payload["request_id"]), recovery=values)
        if isinstance(operation, str) and operation.startswith("backup_"):
            from helper.backup_control import validate_backup

            if type(payload.get("protocol")) is not int or payload["protocol"] != HELPER_PROTOCOL:
                raise ServiceError("unsupported_helper_request", 400)
            try:
                values = validate_backup(payload)
            except (TypeError, AttributeError, KeyError) as error:
                raise ServiceError("invalid_helper_fields", 400) from error
            return cls(operation, request_id(payload["request_id"]), recovery=values)
        if isinstance(operation, str) and operation.startswith("recovery_"):
            from helper.recovery import validate_recovery

            if type(payload.get("protocol")) is not int or payload["protocol"] != HELPER_PROTOCOL:
                raise ServiceError("unsupported_helper_request", 400)
            try:
                values = validate_recovery(payload)
            except (TypeError, AttributeError, KeyError) as error:
                raise ServiceError("invalid_helper_fields", 400) from error
            return cls(operation, request_id(payload["request_id"]), recovery=values)
        fields = {
            "status": set(),
            "telemetry": set(),
            "inventory": set(),
            "outcome": set(),
            "create": {"email", "password"},
            "password": {"email", "password"},
            "delete": {"email", "confirmation", "cutoff", "acknowledged"},
        }
        operation = payload.get("operation")
        if (
            not isinstance(operation, str)
            or operation not in fields
            or type(payload.get("protocol")) is not int
            or payload.get("protocol") != HELPER_PROTOCOL
        ):
            raise ServiceError("unsupported_helper_request", 400)
        if set(payload) != {"protocol", "operation", "request_id"} | fields[operation]:
            raise ServiceError("invalid_helper_fields", 400)
        text_fields = set(payload) - {"protocol", "acknowledged"}
        if any(not isinstance(payload[key], str) for key in text_fields):
            raise ServiceError("invalid_helper_fields", 400)
        identifier = request_id(payload["request_id"])
        email = address(payload["email"]) if "email" in payload else ""
        if operation == "create":
            new_address(email)
        password = mailbox_password(payload["password"]) if "password" in payload else ""
        if operation == "delete" and (
            payload["confirmation"] != email or payload["acknowledged"] is not True
        ):
            raise ServiceError("deletion_confirmation_required", 400)
        return cls(operation, identifier, email, password, str(payload.get("cutoff", "")))
