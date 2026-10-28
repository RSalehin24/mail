"""Restricted Unix client. Lost mutation responses are looked up, never blindly replayed."""

import json
import socket
from pathlib import Path
from typing import Any, Protocol

from shared.errors import ServiceError
from shared.validation import HELPER_LIMIT, HELPER_PROTOCOL


class HelperAccess(Protocol):
    def call(self, request: dict[str, Any]) -> dict[str, Any]: ...


class HelperClient:
    def __init__(self, path: Path, timeout: float) -> None:
        self.path, self.timeout = path, timeout

    def _exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"protocol": HELPER_PROTOCOL, **request}).encode() + b"\n"
        if len(payload) > HELPER_LIMIT:
            raise ServiceError("helper_request_limit", 413)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(str(self.path))
            connection.sendall(payload)
            with connection.makefile("rb") as stream:
                response = stream.readline(131073)
        if len(response) > 131072 or not response.endswith(b"\n"):
            raise ServiceError("invalid_helper_response", 503)
        value = json.loads(response)
        if isinstance(value, dict) and type(value.get("ok")) is not bool:
            raise ServiceError("invalid_helper_response", 503)
        if not isinstance(value, dict) or not value.get("ok"):
            raise ServiceError(
                value.get("error", "helper_unavailable")
                if isinstance(value, dict)
                else "helper_unavailable",
                value.get("status", 503) if isinstance(value, dict) else 503,
            )
        if type(value.get("protocol")) is not int or value["protocol"] != HELPER_PROTOCOL:
            raise ServiceError("incompatible_helper_protocol", 503)
        return value

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._exchange(request)
        except (OSError, ValueError) as error:
            if request["operation"] in {
                "recovery_delete",
                "recovery_restore",
                "recovery_remove",
                "recovery_reconcile",
            }:
                try:
                    return self._exchange(
                        {
                            "operation": "recovery_outcome",
                            "request_id": request["request_id"],
                            "job_id": request["request_id"],
                        }
                    )
                except (OSError, ValueError, ServiceError) as lookup:
                    raise ServiceError("mutation_outcome_unknown", 503) from lookup
            if request["operation"] not in {"create", "password", "delete"}:
                raise ServiceError("helper_unavailable", 503) from error
            return self._lookup(request)

    def _lookup(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._exchange({"operation": "outcome", "request_id": request["request_id"]})
        except (OSError, ValueError, ServiceError) as error:
            raise ServiceError("mutation_outcome_unknown", 503) from error
        outcome = result.get("outcome")
        if outcome is None:
            raise ServiceError("mutation_outcome_unknown", 503)
        if outcome["operation"] != request["operation"] or outcome["email"] != request["email"]:
            raise ServiceError("request_id_conflict", 409)
        return {
            "protocol": HELPER_PROTOCOL,
            "request_id": request["request_id"],
            "state": outcome["state"],
            "postconditions": outcome["postconditions"],
            "warning": "Reconciliation required: some messages may already have been removed."
            if request["operation"] == "delete" and outcome["state"] != "complete"
            else "",
        }
