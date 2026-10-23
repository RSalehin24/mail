"""One invalidation path for password/factor changes and offline restoration."""

import secrets
import sqlite3
from typing import cast

from backend.secrets import SecretKeys
from shared.errors import ServiceError


def admin_state(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM admin_auth WHERE id=1").fetchone()
    if row is None or row["recovery_required"]:
        raise ServiceError("authentication_unavailable", 503)
    return cast(sqlite3.Row, row)


def invalidate_authentication(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM sessions")
    connection.execute("DELETE FROM auth_challenges")
    connection.execute("DELETE FROM otp_codes")
    connection.execute(
        "UPDATE admin_auth SET revision=revision+1, pending_secret=NULL, "
        "pending_expiry=0,pending_session=NULL,enrollment_secret=NULL, "
        "bootstrap_hash=NULL,bootstrap_expiry=0 WHERE id=1"
    )


def replace_recovery_codes(connection: sqlite3.Connection, keys: SecretKeys) -> list[str]:
    codes = [secrets.token_hex(16) for _ in range(5)]
    connection.execute("DELETE FROM recovery_codes")
    connection.executemany(
        "INSERT INTO recovery_codes(verifier) VALUES(?)",
        [(keys.digest(value, "recovery"),) for value in codes],
    )
    return codes


def audit(connection: sqlite3.Connection, event: tuple[float, str, str]) -> None:
    now, action, outcome = event
    connection.execute(
        "INSERT INTO audit_logs(created,action,outcome,request_id) VALUES(?,?,?,?)",
        (now, action, outcome, secrets.token_hex(16)),
    )
