"""Durable administrator email-code preference with current-revision delivery gates."""

import sqlite3
from typing import Any

from backend.auth import Authentication
from backend.auth_state import audit
from backend.relay_state import otp_ready
from shared.errors import ServiceError
from shared.validation import ADMIN_EMAIL, OTP_SENDER


def enabled(connection: sqlite3.Connection, default: bool = False) -> bool:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='security_preferences'"
    ).fetchone()
    if exists:
        row = connection.execute(
            "SELECT value FROM security_preferences WHERE key='email_otp_enabled'"
        ).fetchone()
        if row is not None:
            return bool(row[0])
    return default


class EmailAuthentication:
    def __init__(self, authentication: Authentication) -> None:
        self.auth = authentication

    def status(self) -> dict[str, Any]:
        with self.auth.database.transaction() as connection:
            selected = enabled(connection, self.auth.dependencies.email_enabled)
            ready = otp_ready(connection)
        return {
            "enabled": selected,
            "delivery_verified": ready,
            "available": selected and ready,
            "sender": OTP_SENDER,
            "recipient": ADMIN_EMAIL,
        }

    def save(self, session: str, selected: bool) -> dict[str, Any]:
        with self.auth.database.transaction() as connection:
            self.auth.recent_session(connection, (session, self.auth.dependencies.clock()))
            if selected and not otp_ready(connection):
                raise ServiceError("email_delivery_confirmation_required", 409)
            # An explicit authenticated settings write adds this backward-compatible
            # preference table; public startup never migrates existing installations.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS security_preferences "
                "(key TEXT PRIMARY KEY,value INTEGER NOT NULL CHECK(value IN (0,1)))"
            )
            connection.execute(
                "INSERT INTO security_preferences VALUES('email_otp_enabled',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (int(selected),),
            )
            if not selected:
                connection.execute("DELETE FROM otp_codes")
            audit(
                connection,
                (
                    self.auth.dependencies.clock(),
                    "email-otp",
                    "enabled" if selected else "disabled",
                ),
            )
        return self.status()
