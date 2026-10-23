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

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "HelperRequest":
        fields = {
            "status": set(),
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
