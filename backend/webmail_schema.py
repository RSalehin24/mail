"""Additive, explicitly installed webmail metadata; message bodies remain in IMAP."""

import sqlite3

from backend.database import Database
from shared.errors import ServiceError

SCHEMA = """
CREATE TABLE IF NOT EXISTS webmail_schema (version INTEGER PRIMARY KEY CHECK(version=1));
INSERT OR IGNORE INTO webmail_schema VALUES(1);
CREATE TABLE IF NOT EXISTS mailbox_users (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, inbox_validity INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 0,
 display_name TEXT NOT NULL DEFAULT '', reply_to TEXT NOT NULL DEFAULT '',
 signature TEXT NOT NULL DEFAULT '', theme TEXT NOT NULL DEFAULT 'system',
 totp_secret TEXT NOT NULL DEFAULT '', last_step INTEGER NOT NULL DEFAULT -1,
 pending_secret TEXT, pending_expiry REAL NOT NULL DEFAULT 0, pending_session TEXT,
 created REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS mailbox_user_active ON mailbox_users(email) WHERE active=1;
CREATE TABLE IF NOT EXISTS mailbox_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mailbox_users(id),
 revision INTEGER NOT NULL, created REAL NOT NULL, last_activity REAL NOT NULL,
 absolute_expiry REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS mailbox_recovery_codes (
 user_id TEXT NOT NULL REFERENCES mailbox_users(id), verifier TEXT NOT NULL,
 PRIMARY KEY(user_id,verifier)
);
CREATE TABLE IF NOT EXISTS mailbox_labels (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mailbox_users(id),
 name TEXT NOT NULL, color TEXT NOT NULL, UNIQUE(user_id,name)
);
CREATE TABLE IF NOT EXISTS mailbox_trash (
 user_id TEXT NOT NULL REFERENCES mailbox_users(id), folder TEXT NOT NULL,
 uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL, entered REAL NOT NULL,
 original_folder TEXT, PRIMARY KEY(user_id,folder,uidvalidity,uid)
);
CREATE TABLE IF NOT EXISTS mailbox_sends (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mailbox_users(id),
 state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
 draft_folder TEXT, draft_validity INTEGER, draft_uid INTEGER,
 original_draft TEXT, content_hash TEXT NOT NULL DEFAULT '',
 sent_folder TEXT, sent_validity INTEGER, sent_uid INTEGER,
 error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS webmail_maintenance (
 id INTEGER PRIMARY KEY CHECK(id=1), cleanup_enabled INTEGER NOT NULL DEFAULT 0,
 reconciled REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mailbox_permanent_deletions (
 user_id TEXT NOT NULL REFERENCES mailbox_users(id), folder TEXT NOT NULL,
 uidvalidity INTEGER NOT NULL, uid INTEGER NOT NULL, requested REAL NOT NULL,
 PRIMARY KEY(user_id,folder,uidvalidity,uid)
);
INSERT OR IGNORE INTO webmail_maintenance(id) VALUES(1);
"""


def install(database: Database) -> None:
    # executescript would silently commit the owning transaction. Execute each
    # statement instead, so a killed migration is entirely rolled back.
    with database.transaction() as connection:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)


def available(connection: sqlite3.Connection) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='webmail_schema'"
        ).fetchone()
        is not None
    )


def require_schema(database: Database) -> None:
    with database.transaction() as connection:
        if not available(connection):
            raise ServiceError("webmail_migration_required", 503)
        if [row[0] for row in connection.execute("SELECT version FROM webmail_schema")] != [1]:
            raise ServiceError("incompatible_webmail_schema", 503)


def retire(connection: sqlite3.Connection, email: str) -> None:
    if not available(connection):
        return
    connection.execute(
        "UPDATE mailbox_users SET active=0,revision=revision+1 WHERE email=?", (email,)
    )
    connection.execute(
        "DELETE FROM mailbox_sessions WHERE user_id IN "
        "(SELECT id FROM mailbox_users WHERE email=?)",
        (email,),
    )


def invalidate_restored(connection: sqlite3.Connection) -> None:
    if available(connection):
        connection.execute("DELETE FROM mailbox_sessions")
        connection.execute("DELETE FROM mailbox_permanent_deletions")
        connection.execute("UPDATE mailbox_users SET revision=revision+1,pending_secret=NULL")
        connection.execute("UPDATE webmail_maintenance SET cleanup_enabled=0,reconciled=0")
        connection.execute(
            "UPDATE mailbox_sends SET state='submission_uncertain' WHERE state='submitting'"
        )
        connection.execute(
            "UPDATE mailbox_sends SET state='sent_save_uncertain' WHERE state='saving_sent'"
        )
        connection.execute(
            "UPDATE mailbox_sends SET state='preparation_uncertain' WHERE state='preparing'"
        )
