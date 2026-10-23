"""Durable deduplicated notifications independent of dashboard state and TLS."""

import http.client
import json
import smtplib
import ssl
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json
from shared.smtp import SMTP_HOST, OutboundMessage, SmtpCredentials, authenticated, send_message
from shared.validation import ADMIN_EMAIL, OTP_SENDER, bounded_text, utf8_size


class AlertTransport(Protocol):
    def deliver(self, event: dict[str, Any]) -> None: ...


class SmtpAlertTransport:
    def __init__(self, configuration: dict[str, Any]) -> None:
        self.configuration = configuration

    def deliver(self, event: dict[str, Any]) -> None:
        if self.configuration.get("recipient") != ADMIN_EMAIL:
            raise ServiceError("invalid_alert_recipient", 503)
        credentials = protected_json(Path(self.configuration["credential_reference"]))
        username, credential = credentials.get("username"), credentials.get("credential")
        if not isinstance(username, str) or not isinstance(credential, str):
            raise ServiceError("invalid_alert_credential", 503)
        bounded_text(username, 256)
        if not username or not 1 <= utf8_size(credential) <= 4096:
            raise ServiceError("invalid_alert_credential", 503)
        connection_details = SmtpCredentials(
            SMTP_HOST, self.configuration["port"], username, credential
        )
        name, condition = bounded_text(event["name"], 128), bounded_text(event["condition"], 128)
        message = OutboundMessage(
            OTP_SENDER, f"Mail Admin: {name} — {condition}", json.dumps(event, sort_keys=True)
        )
        try:
            with authenticated(connection_details, 5) as connection:
                send_message(connection, message)
        except (OSError, UnicodeError, smtplib.SMTPException) as error:
            raise ServiceError("alert_delivery_failed", 503) from error


def alert_transport(configuration: dict[str, Any]) -> AlertTransport:
    if configuration.get("provider") == "brevo_smtp":
        return SmtpAlertTransport(configuration)
    if configuration.get("provider", "https_webhook") == "https_webhook":
        return HttpsAlertTransport(configuration)
    raise ServiceError("alert_provider_required", 503)


class HttpsAlertTransport:
    def __init__(self, configuration: dict[str, Any]) -> None:
        self.configuration = configuration

    def deliver(self, event: dict[str, Any]) -> None:
        endpoint = urlsplit(self.configuration["endpoint"])
        if (
            endpoint.scheme != "https"
            or endpoint.username
            or endpoint.password
            or not endpoint.hostname
        ):
            raise ServiceError("invalid_alert_endpoint", 503)
        token = protected_bytes(Path(self.configuration["credential_reference"])).decode().strip()
        connection = http.client.HTTPSConnection(
            endpoint.hostname, endpoint.port or 443, timeout=5, context=ssl.create_default_context()
        )
        try:
            body = json.dumps({"recipient": self.configuration["recipient"], "event": event})
            connection.request(
                "POST",
                endpoint.path or "/",
                body,
                {"Content-Type": "application/json", "Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            response.read(4096)
            if not 200 <= response.status < 300:
                raise ServiceError("alert_delivery_failed", 503)
        finally:
            connection.close()


class Alerts:
    def __init__(
        self,
        path: Path,
        dependencies: tuple[AlertTransport, Callable[[], float]],
        lock_policy: LockPolicy | None = None,
    ) -> None:
        self.path = path
        self.transport, self.clock = dependencies
        self.lock_policy = lock_policy or LockPolicy(5)

    def observe(self, name: str, condition: str) -> None:
        with HostLock(self.path.with_suffix(".lock"), self.lock_policy):
            self._observe(name, condition)

    def _observe(self, name: str, condition: str) -> None:
        state = protected_json(self.path)
        previous = state.get(name)
        if previous and previous["condition"] == condition:
            return
        pending = self._pending(previous)
        if len(pending) >= 256:
            raise ServiceError("notification_outbox_full", 503)
        state[name] = {
            "event_id": str(uuid.uuid4()),
            "condition": condition,
            "created": self.clock(),
            "attempts": 0,
            "next_attempt": self.clock(),
            "delivered": False,
            "pending": pending,
        }
        atomic_json(self.path, state)

    @staticmethod
    def _pending(previous: dict[str, Any] | None) -> list[dict[str, Any]]:
        if previous is None:
            return []
        pending = list(previous.get("pending", []))
        if not previous["delivered"]:
            pending.append({key: value for key, value in previous.items() if key != "pending"})
        return pending

    def flush(self) -> None:
        with HostLock(self.path.with_suffix(".lock"), self.lock_policy):
            self._flush()

    def _flush(self) -> None:
        state = protected_json(self.path)
        for name, record in state.items():
            self._deliver(name, record)
            record["pending"] = [
                event for event in record.get("pending", []) if not event["delivered"]
            ]
            atomic_json(self.path, state)
        if any(not record["delivered"] for record in state.values()):
            raise ServiceError("notifications_pending", 503)

    def _deliver(self, name: str, current: dict[str, Any]) -> None:
        for record in [*current.get("pending", []), current]:
            if record["delivered"]:
                continue
            if record["next_attempt"] > self.clock():
                return
            record["attempts"] += 1
            try:
                self.transport.deliver(
                    {
                        "id": record.get("event_id"),
                        "name": name,
                        "condition": record["condition"],
                        "observed_at": record["created"],
                    }
                )
                record["delivered"] = True
            except (OSError, ServiceError):
                record["next_attempt"] = self.clock() + min(
                    3600, 30 * 2 ** min(record["attempts"], 7)
                )
                return
