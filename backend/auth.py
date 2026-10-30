"""Password challenges and opaque sessions; password verification alone grants nothing."""

import hmac
import secrets
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from backend.auth_state import admin_state, audit
from backend.budgets import BudgetReservation, Budgets
from backend.database import Database
from backend.factors import FactorAttempt, Factors
from backend.relay_state import otp_ready
from backend.secrets import SecretKeys
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import master_password

OTP_GENERATION_ATTEMPTS = 8


@dataclass(frozen=True)
class AuthDependencies:
    database: Database
    keys: SecretKeys
    hasher: PasswordHasher
    clock: Callable[[], float]
    email_enabled: bool = False


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    peer: str


@dataclass(frozen=True)
class Verification:
    challenge: str
    factor: str
    code: str
    peer: str
    session: str | None = None


class Authentication:
    def __init__(self, dependencies: AuthDependencies) -> None:
        self.dependencies = dependencies
        self.database = dependencies.database
        self.keys = dependencies.keys
        self.factors = Factors(self.keys)
        self.budgets = Budgets(self.database)

    def reserve(self, peer: str) -> float:
        now = self.dependencies.clock()
        self.budgets.reserve(BudgetReservation("auth", peer, now))
        return now

    def password_challenge(self, credentials: Credentials, session: str | None = None) -> str:
        now = self.reserve(credentials.peer)
        master_password(credentials.password)
        with self.database.transaction() as connection:
            before = admin_state(connection)
        if not before["initialized"] or credentials.email != site().admin_email:
            raise ServiceError("authentication_failed", 401)
        self._check_password(before["password_hash"], credentials.password)
        now = self.dependencies.clock()
        with self.database.transaction() as connection:
            current = admin_state(connection)
            if current["revision"] != before["revision"]:
                raise ServiceError("authentication_failed", 401)
            binding = self._session(connection, (session, now))["token_hash"] if session else None
            return self._new_challenge(connection, (current["revision"], binding, now))

    def _check_password(self, password_hash: str, password: str) -> None:
        try:
            self.dependencies.hasher.verify(password_hash, password)
        except VerificationError as error:
            raise ServiceError("authentication_failed", 401) from error

    def _new_challenge(
        self, connection: sqlite3.Connection, values: tuple[int, str | None, float]
    ) -> str:
        revision, binding, now = values
        token = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO auth_challenges VALUES(?,?,?,?,?,?)",
            (
                self.keys.digest(token, "challenge"),
                "reauth" if binding else "login",
                binding,
                revision,
                now,
                now + 300,
            ),
        )
        return token

    def verify(self, verification: Verification, purpose: str) -> str | None:
        self.reserve(verification.peer)
        token_hash = self.keys.digest(verification.challenge, "challenge")
        issued = None
        with self.database.transaction() as connection:
            now = self.dependencies.clock()
            challenge = self.challenge(connection, (token_hash, purpose, verification.session, now))
            if verification.factor == "email" and not self._email_ready(connection):
                raise ServiceError("email_otp_unavailable", 403)
            attempt = FactorAttempt(verification.factor, verification.code, token_hash)
            valid = self.factors.consume(connection, (attempt, now))
            if valid:
                issued = self._complete(connection, (challenge, verification.session, now))
            audit(connection, (now, purpose, "verified" if valid else "rejected"))
        if not valid:
            raise ServiceError("authentication_failed", 401)
        return issued

    def challenge(
        self, connection: sqlite3.Connection, values: tuple[str, str, str | None, float]
    ) -> sqlite3.Row:
        token_hash, purpose, session, now = values
        admin = admin_state(connection)
        row = connection.execute(
            "SELECT * FROM auth_challenges WHERE token_hash=?", (token_hash,)
        ).fetchone()
        if row is None or row["expiry"] <= now or row["revision"] != admin["revision"]:
            raise ServiceError("authentication_failed", 401)
        if row["purpose"] != purpose:
            raise ServiceError("authentication_failed", 401)
        if purpose == "reauth" and (
            not session
            or row["session_hash"] != self._session(connection, (session, now))["token_hash"]
        ):
            raise ServiceError("authentication_failed", 401)
        return cast(sqlite3.Row, row)

    def _complete(
        self, connection: sqlite3.Connection, values: tuple[sqlite3.Row, str | None, float]
    ) -> str | None:
        challenge, session, now = values
        connection.execute(
            "DELETE FROM auth_challenges WHERE token_hash=?", (challenge["token_hash"],)
        )
        if challenge["purpose"] == "reauth":
            connection.execute(
                "UPDATE sessions SET recent_auth=? WHERE token_hash=?",
                (now, self.keys.digest(session or "", "session")),
            )
            return None
        token = secrets.token_urlsafe(32)
        connection.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,NULL)",
            (self.keys.digest(token, "session"), challenge["revision"], now, now, now + 43200),
        )
        return token

    def _session(
        self, connection: sqlite3.Connection, values: tuple[str | None, float]
    ) -> sqlite3.Row:
        token, now = values
        admin = admin_state(connection)
        row = connection.execute(
            "SELECT * FROM sessions WHERE token_hash=?", (self.keys.digest(token or "", "session"),)
        ).fetchone()
        if (
            row is None
            or row["revision"] != admin["revision"]
            or row["absolute_expiry"] <= now
            or row["last_activity"] + 900 <= now
        ):
            raise ServiceError("authentication_required", 401)
        return cast(sqlite3.Row, row)

    def require_session(self, token: str | None, recent: bool = False) -> str:
        now = self.dependencies.clock()
        with self.database.transaction() as connection:
            row = self._session(connection, (token, now))
            if recent and (row["recent_auth"] is None or row["recent_auth"] + 300 <= now):
                raise ServiceError("recent_authentication_required", 403)
            connection.execute(
                "UPDATE sessions SET last_activity=? WHERE token_hash=?", (now, row["token_hash"])
            )
            return str(row["token_hash"])

    def logout(self, token: str | None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (self.keys.digest(token or "", "session"),),
            )
            audit(connection, (self.dependencies.clock(), "logout", "revoked"))

    def recent_session(
        self, connection: sqlite3.Connection, values: tuple[str, float]
    ) -> sqlite3.Row:
        row = self._session(connection, values)
        if row["recent_auth"] is None or row["recent_auth"] + 300 <= values[1]:
            raise ServiceError("recent_authentication_required", 403)
        return row

    def send_otp(self, verification: Verification, sender: Callable[[str], None]) -> None:
        now = self.dependencies.clock()
        token_hash = self.keys.digest(verification.challenge, "challenge")
        purpose = "reauth" if verification.session else "login"
        with self.database.transaction() as connection:
            self.challenge(connection, (token_hash, purpose, verification.session, now))
            if not self._email_ready(connection):
                raise ServiceError("email_otp_unavailable", 403)
        self.budgets.reserve(BudgetReservation("send", verification.peer, now))
        code, verifier = self._store_otp(verification, now)
        try:
            sender(code)
        except (OSError, ServiceError) as error:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE otp_codes SET consumed=1 WHERE challenge_hash=? AND verifier=?",
                    (token_hash, verifier),
                )
            raise ServiceError("email_delivery_failed", 503) from error

    def _store_otp(self, verification: Verification, now: float) -> tuple[str, str]:
        token_hash = self.keys.digest(verification.challenge, "challenge")
        purpose = "reauth" if verification.session else "login"
        with self.database.transaction() as connection:
            self.challenge(connection, (token_hash, purpose, verification.session, now))
            if not self._email_ready(connection):
                raise ServiceError("email_otp_unavailable", 403)
            previous = connection.execute(
                "SELECT resend_after,verifier FROM otp_codes WHERE challenge_hash=?", (token_hash,)
            ).fetchone()
            if previous and previous[0] > now:
                raise ServiceError("otp_resend_limited", 429, int(previous[0] - now + 0.999))
            code, verifier = self._otp_material(previous[1] if previous else None, token_hash)
            connection.execute(
                "INSERT OR REPLACE INTO otp_codes VALUES(?,?,?,0,?,0)",
                (token_hash, verifier, now + 300, now + 60),
            )
            return code, verifier

    def _otp_material(self, previous: str | None, challenge: str) -> tuple[str, str]:
        for _ in range(OTP_GENERATION_ATTEMPTS):
            code = f"{secrets.randbelow(1000000):06d}"
            verifier = self.keys.digest(challenge + ":" + code, "email-otp")
            if previous is None or not hmac.compare_digest(previous, verifier):
                return code, verifier
        raise ServiceError("otp_generation_failed", 503)

    def _email_ready(self, connection: sqlite3.Connection) -> bool:
        from backend.email_auth import enabled

        if not enabled(connection, self.dependencies.email_enabled):
            return False
        return otp_ready(connection)

    def email_available(self) -> bool:
        with self.database.transaction() as connection:
            return self._email_ready(connection)
