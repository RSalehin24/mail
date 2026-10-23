"""Single-use factors; caller owns the same transaction as session/grant issuance."""

import hmac
import re
import sqlite3
from dataclasses import dataclass

import pyotp

from backend.secrets import SecretKeys


def matching_step(secret: str, code: str, now: float) -> int | None:
    if re.fullmatch(r"[0-9]{6}", code) is None:
        return None
    current = int(now // 30)
    matches = [
        step
        for step in range(current - 1, current + 2)
        if hmac.compare_digest(pyotp.TOTP(secret).at(step * 30), code)
    ]
    return max(matches) if matches else None


@dataclass(frozen=True)
class FactorAttempt:
    kind: str
    code: str
    challenge_hash: str


class Factors:
    def __init__(self, keys: SecretKeys) -> None:
        self.keys = keys

    def consume(self, connection: sqlite3.Connection, attempt: tuple[FactorAttempt, float]) -> bool:
        factor, now = attempt
        if factor.kind == "totp":
            return self._totp(connection, (factor.code, now))
        if factor.kind == "recovery":
            result = connection.execute(
                "UPDATE recovery_codes SET consumed=1 WHERE verifier=? AND consumed=0",
                (self.keys.digest(factor.code, "recovery"),),
            )
            return result.rowcount == 1
        if factor.kind == "email":
            return self._email(connection, (factor, now))
        return False

    def _totp(self, connection: sqlite3.Connection, attempt: tuple[str, float]) -> bool:
        code, now = attempt
        row = connection.execute(
            "SELECT totp_secret,last_step FROM admin_auth WHERE id=1"
        ).fetchone()
        if not row["totp_secret"]:
            return False
        step = matching_step(self.keys.open(row["totp_secret"], "totp"), code, now)
        if step is None or step <= row["last_step"]:
            return False
        connection.execute("UPDATE admin_auth SET last_step=? WHERE id=1", (step,))
        return True

    def _email(self, connection: sqlite3.Connection, attempt: tuple[FactorAttempt, float]) -> bool:
        factor, now = attempt
        row = connection.execute(
            "SELECT * FROM otp_codes WHERE challenge_hash=?", (factor.challenge_hash,)
        ).fetchone()
        if row is None or row["consumed"] or row["expiry"] <= now or row["attempts"] >= 5:
            return False
        connection.execute(
            "UPDATE otp_codes SET attempts=attempts+1 WHERE challenge_hash=?",
            (factor.challenge_hash,),
        )
        valid = hmac.compare_digest(
            row["verifier"],
            self.keys.digest(factor.challenge_hash + ":" + factor.code, "email-otp"),
        )
        if valid:
            connection.execute(
                "UPDATE otp_codes SET consumed=1 WHERE challenge_hash=?", (factor.challenge_hash,)
            )
        return valid
