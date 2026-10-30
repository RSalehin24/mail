"""Bootstrap-gated TOTP enrollment. No initialized database can reopen setup."""

import base64
import hmac
import secrets
import sqlite3

import pyotp
import qrcode
import qrcode.image.svg

from backend.auth import Authentication
from backend.auth_state import admin_state, audit, invalidate_authentication, replace_recovery_codes
from backend.factors import matching_step
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import master_password


def qr_material(secret: str) -> dict[str, str]:
    uri = pyotp.TOTP(secret).provisioning_uri(site().admin_email, issuer_name="Mail Admin")
    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
    encoded = base64.b64encode(image.to_string()).decode()
    return {"secret": secret, "uri": uri, "qr": "data:image/svg+xml;base64," + encoded}


class Enrollment:
    def __init__(self, authentication: Authentication) -> None:
        self.auth = authentication

    def issue_bootstrap(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.auth.database.transaction() as connection:
            row = admin_state(connection)
            if row["initialized"]:
                raise ServiceError("setup_closed", 403)
            connection.execute(
                "UPDATE admin_auth SET bootstrap_hash=?,bootstrap_expiry=?, "
                "enrollment_secret=NULL WHERE id=1",
                (self.auth.keys.digest(token, "bootstrap"), self.auth.dependencies.clock() + 1800),
            )
        return token

    def _bootstrap(self, connection: sqlite3.Connection, values: tuple[str, float]) -> sqlite3.Row:
        token, now = values
        row = admin_state(connection)
        expected = row["bootstrap_hash"] or ""
        valid = hmac.compare_digest(expected, self.auth.keys.digest(token, "bootstrap"))
        if row["initialized"] or not valid or row["bootstrap_expiry"] <= now:
            raise ServiceError("setup_denied", 403)
        return row

    def begin(self, token: str, peer: str) -> dict[str, str]:
        now = self.auth.reserve(peer)
        with self.auth.database.transaction() as connection:
            row = self._bootstrap(connection, (token, now))
            if row["enrollment_secret"] and row["enrollment_expiry"] > now:
                secret = self.auth.keys.open(row["enrollment_secret"], "enrollment")
            else:
                secret = pyotp.random_base32()
                connection.execute(
                    "UPDATE admin_auth SET enrollment_secret=?,enrollment_expiry=?",
                    (self.auth.keys.seal(secret, "enrollment"), now + 300),
                )
        return qr_material(secret)

    def confirm(self, enrollment: dict[str, str], peer: str) -> list[str]:
        self.auth.reserve(peer)
        with self.auth.database.transaction() as connection:
            self._bootstrap(connection, (enrollment["token"], self.auth.dependencies.clock()))
        password_hash = self.auth.dependencies.hasher.hash(master_password(enrollment["password"]))
        with self.auth.database.transaction() as connection:
            now = self.auth.dependencies.clock()
            row = self._bootstrap(connection, (enrollment["token"], now))
            if not row["enrollment_secret"] or row["enrollment_expiry"] <= now:
                raise ServiceError("setup_denied", 403)
            secret = self.auth.keys.open(row["enrollment_secret"], "enrollment")
            step = matching_step(secret, enrollment["code"], now)
            if step is None:
                raise ServiceError("authentication_failed", 401)
            invalidate_authentication(connection)
            connection.execute(
                "UPDATE admin_auth SET initialized=1,password_hash=?,totp_secret=?, "
                "last_step=? WHERE id=1",
                (password_hash, self.auth.keys.seal(secret, "totp"), step),
            )
            codes = replace_recovery_codes(connection, self.auth.keys)
            audit(connection, (now, "setup", "initialized"))
            return codes
