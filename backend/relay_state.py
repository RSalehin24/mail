"""Readiness is evidence for a confirmed delivery on the current saved revision."""

import sqlite3

from shared.installation import site


def otp_ready(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT 1 FROM relay_settings r JOIN relay_tests t ON "
        "r.readiness_test=t.id AND r.revision=t.revision WHERE "
        "r.id=1 AND t.sender=? AND t.recipient=? AND t.accepted=1 "
        "AND t.confirmed=1",
        (site().otp_sender, site().admin_email),
    ).fetchone()
    return row is not None
