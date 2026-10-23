"""Sensitive security changes recheck the session grant in their commit transaction."""

import pyotp

from backend.auth import Authentication
from backend.auth_state import admin_state, audit, invalidate_authentication, replace_recovery_codes
from backend.enrollment import qr_material
from backend.factors import matching_step
from shared.errors import ServiceError
from shared.validation import master_password


class Security:
    def __init__(self, authentication: Authentication) -> None:
        self.auth = authentication

    def change_password(self, session: str, password: str) -> None:
        self.auth.require_session(session, recent=True)
        encoded = self.auth.dependencies.hasher.hash(master_password(password))
        now = self.auth.dependencies.clock()
        with self.auth.database.transaction() as connection:
            self.auth.recent_session(connection, (session, now))
            invalidate_authentication(connection)
            connection.execute("UPDATE admin_auth SET password_hash=? WHERE id=1", (encoded,))
            audit(connection, (now, "master-password", "changed"))

    def recovery_codes(self, session: str) -> list[str]:
        now = self.auth.dependencies.clock()
        with self.auth.database.transaction() as connection:
            self.auth.recent_session(connection, (session, now))
            codes = replace_recovery_codes(connection, self.auth.keys)
            invalidate_authentication(connection)
            audit(connection, (now, "recovery-codes", "replaced"))
            return codes

    def begin_totp(self, session: str) -> dict[str, str]:
        now = self.auth.dependencies.clock()
        secret = pyotp.random_base32()
        with self.auth.database.transaction() as connection:
            row = self.auth.recent_session(connection, (session, now))
            connection.execute(
                "UPDATE admin_auth SET pending_secret=?,pending_expiry=?, "
                "pending_session=? WHERE id=1",
                (self.auth.keys.seal(secret, "replacement"), now + 600, row["token_hash"]),
            )
        return qr_material(secret)

    def confirm_totp(self, session: str, attempt: tuple[str, str]) -> list[str]:
        code, peer = attempt
        now = self.auth.reserve(peer)
        with self.auth.database.transaction() as connection:
            current_session = self.auth.recent_session(connection, (session, now))
            admin = admin_state(connection)
            if (
                not admin["pending_secret"]
                or admin["pending_expiry"] <= now
                or admin["pending_session"] != current_session["token_hash"]
            ):
                raise ServiceError("replacement_expired", 403)
            secret = self.auth.keys.open(admin["pending_secret"], "replacement")
            step = matching_step(secret, code, now)
            if step is None or step <= admin["last_step"]:
                raise ServiceError("authentication_failed", 401)
            invalidate_authentication(connection)
            connection.execute(
                "UPDATE admin_auth SET totp_secret=?,last_step=? WHERE id=1",
                (self.auth.keys.seal(secret, "totp"), step),
            )
            codes = replace_recovery_codes(connection, self.auth.keys)
            audit(connection, (now, "totp", "replaced"))
            return codes
