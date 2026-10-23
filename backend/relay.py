"""Revision-bound Brevo readiness. SMTP acceptance and observed receipt remain distinct."""

import smtplib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from backend.auth_state import audit
from backend.budgets import BudgetReservation, Budgets
from backend.database import Database
from backend.quota import read_quota
from backend.relay_state import otp_ready
from backend.secrets import SecretKeys
from shared.errors import ServiceError
from shared.smtp import SMTP_HOST, OutboundMessage, SmtpCredentials, authenticated, send_message
from shared.validation import ADMIN_EMAIL, OTP_SENDER, address, bounded_text, utf8_size


@dataclass(frozen=True)
class SavedRelay:
    host: str
    port: int
    username: str
    credential: str
    revision: int
    delivery_confirmed: bool = False


class RelayTransport(Protocol):
    def perform(self, configuration: SavedRelay, message: OutboundMessage | None) -> None: ...


class SmtpTransport:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def perform(self, configuration: SavedRelay, message: OutboundMessage | None) -> None:
        credentials = SmtpCredentials(
            configuration.host, configuration.port, configuration.username, configuration.credential
        )
        try:
            with authenticated(credentials, self.timeout) as connection:
                if message:
                    send_message(connection, message)
        except (OSError, UnicodeError, smtplib.SMTPException) as error:
            raise ServiceError("relay_tls_auth_or_send_failed", 502) from error


@dataclass(frozen=True)
class RelayDependencies:
    database: Database
    keys: SecretKeys
    transport: RelayTransport
    clock: Callable[[], float]


class Relay:
    def __init__(self, dependencies: RelayDependencies) -> None:
        self.dependencies = dependencies
        self.database, self.keys = dependencies.database, dependencies.keys
        self.budgets = Budgets(self.database)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        if (
            payload["host"] != SMTP_HOST
            or type(payload["port"]) is not int
            or payload["port"] not in {465, 587}
        ):
            raise ServiceError("invalid_relay_endpoint", 400)
        expected = "starttls" if payload["port"] == 587 else "tls"
        if payload["tls_mode"] != expected:
            raise ServiceError("invalid_relay_tls_mode", 400)
        username = bounded_text(payload["username"], 256)
        if not username:
            raise ServiceError("relay_username_required", 400)
        with self.database.transaction() as connection:
            previous = connection.execute("SELECT * FROM relay_settings WHERE id=1").fetchone()
            credential = payload.get("credential")
            if credential is None and previous:
                credential = self.keys.open(previous["credential"], "relay-credential")
            if not isinstance(credential, str) or not 1 <= utf8_size(credential) <= 4096:
                raise ServiceError("relay_credential_required", 400)
            api_key = payload.get("api_key")
            if api_key is None:
                api_key = self.keys.open(previous["api_key"], "relay-api") if previous else ""
            if not isinstance(api_key, str):
                raise ServiceError("invalid_relay_api_key", 400)
            bounded_text(api_key, 4096)
            revision = previous["revision"] + 1 if previous else 1
            connection.execute(
                "INSERT OR REPLACE INTO relay_settings VALUES(1,?,?,?,?,?,?,NULL)",
                (
                    SMTP_HOST,
                    payload["port"],
                    username,
                    self.keys.seal(credential, "relay-credential"),
                    self.keys.seal(api_key, "relay-api"),
                    revision,
                ),
            )
            connection.execute("UPDATE mailboxes SET outbound_test=NULL")
            connection.execute("DELETE FROM otp_codes")
            audit(connection, (self.dependencies.clock(), "relay-settings", "saved"))
        return self.masked()

    def _configuration(self) -> SavedRelay:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM relay_settings WHERE id=1").fetchone()
            confirmed = otp_ready(connection)
        if row is None:
            raise ServiceError("relay_not_configured", 409)
        if row["host"] != SMTP_HOST or row["port"] not in {465, 587}:
            raise ServiceError("relay_configuration_invalid", 503)
        return SavedRelay(
            row["host"],
            row["port"],
            row["username"],
            self.keys.open(row["credential"], "relay-credential"),
            row["revision"],
            confirmed,
        )

    def masked(self) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM relay_settings WHERE id=1").fetchone()
            if row is None:
                return {
                    "configured": False,
                    "host": SMTP_HOST,
                    "port": 587,
                    "otp_ready": False,
                    "username": "",
                    "credential": "",
                    "revision": 0,
                    "tests": [],
                }
            tests = [
                dict(test)
                for test in connection.execute(
                    "SELECT * FROM relay_tests WHERE revision=? ORDER BY created DESC LIMIT 10",
                    (row["revision"],),
                )
            ]
            confirmed = otp_ready(connection)
        return {
            "configured": True,
            "host": row["host"],
            "port": row["port"],
            "username": row["username"],
            "credential": "••••••••",
            "api_key": "••••••••",
            "revision": row["revision"],
            "otp_ready": confirmed,
            "tests": tests,
        }

    def otp_ready(self) -> bool:
        with self.database.transaction() as connection:
            return otp_ready(connection)

    def test(self, sender: str, values: tuple[str, str]) -> dict[str, Any]:
        mode, peer = values
        sender = address(sender)
        if mode not in {"connection", "delivery"}:
            raise ServiceError("invalid_relay_test_mode", 400)
        now, identifier = self.dependencies.clock(), str(uuid.uuid4())
        if mode == "delivery":
            self.budgets.reserve(BudgetReservation("send", peer, now))
        configuration = self._configuration()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO relay_tests VALUES(?,?,?,?,0,0,0,?)",
                (identifier, configuration.revision, sender, ADMIN_EMAIL, now),
            )
        message = (
            OutboundMessage(
                sender,
                "Mail Admin delivery test " + identifier,
                "Delivery test: "
                + identifier
                + "\nConfirm this identifier in Mail Admin after receipt.",
            )
            if mode == "delivery"
            else None
        )
        succeeded = True
        try:
            self.dependencies.transport.perform(configuration, message)
        except (OSError, ServiceError):
            succeeded = False
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE relay_tests SET connection_ok=?,accepted=? WHERE id=?",
                (int(succeeded), int(succeeded and mode == "delivery"), identifier),
            )
            audit(connection, (now, "relay-test", "smtp-accepted" if succeeded else "failed"))
        return {
            "test_id": identifier,
            "revision": configuration.revision,
            "connection_ok": succeeded,
            "smtp_accepted": succeeded and mode == "delivery",
            "delivery_confirmed": False,
            "recipient": ADMIN_EMAIL,
            "sender": sender,
        }

    def confirm(self, identifier: str) -> dict[str, bool]:
        with self.database.transaction() as connection:
            test = connection.execute(
                "SELECT * FROM relay_tests WHERE id=?", (identifier,)
            ).fetchone()
            settings = connection.execute(
                "SELECT revision FROM relay_settings WHERE id=1"
            ).fetchone()
            if (
                not test
                or not settings
                or not test["accepted"]
                or test["recipient"] != ADMIN_EMAIL
                or test["revision"] != settings["revision"]
            ):
                raise ServiceError("relay_test_not_confirmable", 409)
            connection.execute("UPDATE relay_tests SET confirmed=1 WHERE id=?", (identifier,))
            if test["sender"] == OTP_SENDER:
                connection.execute(
                    "UPDATE relay_settings SET readiness_test=? WHERE id=1", (identifier,)
                )
            connection.execute(
                "UPDATE mailboxes SET outbound_test=? WHERE email=?", (identifier, test["sender"])
            )
            audit(connection, (self.dependencies.clock(), "relay-receipt", "confirmed"))
        return {"delivery_confirmed": True}

    def reveal(self) -> str:
        configuration = self._configuration()
        with self.database.transaction() as connection:
            audit(connection, (self.dependencies.clock(), "relay-credential", "revealed"))
        return configuration.credential

    def quota(self) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT api_key FROM relay_settings WHERE id=1").fetchone()
        api_key = self.keys.open(row["api_key"], "relay-api") if row else ""
        if not api_key:
            return {"state": "not_configured", "plans": []}
        return read_quota(api_key)

    def send_otp(self, code: str) -> None:
        configuration = self._configuration()
        if not configuration.delivery_confirmed:
            raise ServiceError("email_otp_unavailable", 403)
        self.dependencies.transport.perform(
            configuration,
            OutboundMessage(
                OTP_SENDER, "Mail Admin verification code", "Your five-minute code: " + code
            ),
        )
