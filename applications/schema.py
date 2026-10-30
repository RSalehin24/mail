"""Explicit additive migration for independent application state."""

from backend.database import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS application_state (
 id INTEGER PRIMARY KEY CHECK(id=1), kind TEXT NOT NULL, version INTEGER NOT NULL,
 configured INTEGER NOT NULL DEFAULT 0, settings TEXT NOT NULL DEFAULT '{}',
 recovery_epoch TEXT NOT NULL, cleanup_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS managed_mailbox_identities (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, native_id INTEGER NOT NULL,
 inbox_validity INTEGER NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL,
 credential_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS managed_active_email
 ON managed_mailbox_identities(email) WHERE state != 'retired';
CREATE TABLE IF NOT EXISTS integration_enrollments (
 token_hash TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, expires REAL NOT NULL,
 used INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS integration_credentials (
 id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, domain TEXT NOT NULL,
 fingerprint TEXT NOT NULL UNIQUE, verifier TEXT NOT NULL, expires REAL NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0, replaces TEXT, activated INTEGER NOT NULL DEFAULT 1,
 created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS remote_pairing (
 id INTEGER PRIMARY KEY CHECK(id=1), secret TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS integration_trash_events (
 installation_id TEXT NOT NULL, id TEXT NOT NULL, mailbox_id TEXT NOT NULL,
 payload TEXT NOT NULL, received REAL NOT NULL, PRIMARY KEY(installation_id,id)
);
CREATE TABLE IF NOT EXISTS managed_trash (
 mailbox_id TEXT NOT NULL REFERENCES managed_mailbox_identities(id),
 folder TEXT NOT NULL, uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL,
 entered REAL NOT NULL, acknowledged REAL NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(mailbox_id,folder,uidvalidity,uid)
);
CREATE TABLE IF NOT EXISTS trash_outbox (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
 mailbox_id TEXT NOT NULL, payload TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0
);
"""


def install(database: Database, kind: str) -> None:
    if kind not in {"management", "webmail"}:
        raise ValueError("Unknown application")
    with database.transaction() as connection:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO application_state(id,kind,version,recovery_epoch) "
            "VALUES(1,?,1,?)",
            (kind, database.identity),
        )
        row = connection.execute("SELECT kind,version FROM application_state").fetchone()
        if tuple(row) != (kind, 1):
            raise ValueError("Incompatible application database")
        managed = {
            row[1] for row in connection.execute("PRAGMA table_info(managed_mailbox_identities)")
        }
        if "credential_fingerprint" not in managed:
            connection.execute(
                "ALTER TABLE managed_mailbox_identities "
                "ADD COLUMN credential_fingerprint TEXT NOT NULL DEFAULT ''"
            )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(mailbox_users)")}
        if "authority_revision" not in columns:
            connection.execute(
                "ALTER TABLE mailbox_users ADD COLUMN authority_revision INTEGER NOT NULL DEFAULT 0"
            )
