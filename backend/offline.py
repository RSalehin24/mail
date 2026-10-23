"""Offline recovery logic; host coordination stops the dashboard before invocation."""

import sqlite3

from backend.auth import Authentication
from backend.auth_state import audit, invalidate_authentication, replace_recovery_codes
from backend.factors import matching_step
from shared.errors import ServiceError
from shared.validation import master_password


class OfflineRecovery:
    def __init__(self, authentication: Authentication) -> None:
        self.auth = authentication

    def begin(self) -> None:
        with self.auth.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET recovery_required=1 WHERE id=1")
            audit(connection, (self.auth.dependencies.clock(), "offline-recovery", "started"))

    def prepare_restore(self) -> None:
        with self.auth.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET recovery_required=1 WHERE id=1")
            self._invalidate_state(connection)
            audit(connection, (self.auth.dependencies.clock(), "restore", "authentication_closed"))

    def complete(self, password: str, factor: tuple[str, str]) -> list[str]:
        secret, code = factor
        encoded = self.auth.dependencies.hasher.hash(master_password(password))
        with self.auth.database.transaction() as connection:
            now = self.auth.dependencies.clock()
            row = connection.execute("SELECT * FROM admin_auth WHERE id=1").fetchone()
            step = matching_step(secret, code, now)
            if not row["recovery_required"] or step is None or step <= row["last_step"]:
                raise ServiceError("recovery_confirmation_failed", 403)
            codes = self._invalidate_state(connection)
            connection.execute(
                "UPDATE admin_auth SET initialized=1,password_hash=?,totp_secret=?, "
                "last_step=?,recovery_required=0 WHERE id=1",
                (encoded, self.auth.keys.seal(secret, "totp"), step),
            )
            audit(connection, (now, "offline-recovery", "completed"))
            return codes

    def _invalidate_state(self, connection: sqlite3.Connection) -> list[str]:
        invalidate_authentication(connection)
        codes = replace_recovery_codes(connection, self.auth.keys)
        connection.execute("UPDATE relay_settings SET readiness_test=NULL")
        connection.execute("UPDATE mailboxes SET outbound_test=NULL")
        return codes
