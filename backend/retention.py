"""Bounded housekeeping, preserving security watermarks and current readiness."""

from backend.database import Database

HISTORY_SECONDS = 90 * 86400
BATCH_SIZE = 500


def cleanup(database: Database, now: float) -> None:
    with database.transaction() as connection:
        connection.execute(
            "DELETE FROM sessions WHERE absolute_expiry<=? OR last_activity<=?", (now, now - 900)
        )
        connection.execute("DELETE FROM auth_challenges WHERE expiry<=?", (now,))
        connection.execute("DELETE FROM otp_codes WHERE expiry<=? OR consumed=1", (now,))
        connection.execute("DELETE FROM auth_rate_limits WHERE reserved_at<=?", (now - 900,))
        connection.execute(
            "UPDATE admin_auth SET pending_secret=NULL, pending_session=NULL "
            "WHERE pending_expiry<=?",
            (now,),
        )
        connection.execute(
            "UPDATE admin_auth SET enrollment_secret=NULL WHERE enrollment_expiry<=?", (now,)
        )
        connection.execute(
            "DELETE FROM audit_logs WHERE id IN (SELECT id FROM audit_logs "
            "WHERE created<? LIMIT ?)",
            (now - HISTORY_SECONDS, BATCH_SIZE),
        )
        connection.execute(
            "DELETE FROM relay_tests WHERE id IN (SELECT id FROM relay_tests "
            "WHERE created<? AND id NOT IN (SELECT readiness_test FROM relay_settings "
            "WHERE readiness_test IS NOT NULL) AND id NOT IN "
            "(SELECT outbound_test FROM mailboxes WHERE outbound_test IS NOT NULL) "
            "LIMIT ?)",
            (now - HISTORY_SECONDS, BATCH_SIZE),
        )
