"""Initial explicit schema migration. Public startup never creates state."""

INITIAL_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE installation (id INTEGER PRIMARY KEY CHECK(id=1), identity TEXT NOT NULL);
CREATE TABLE admin_auth (
 id INTEGER PRIMARY KEY CHECK(id=1), initialized INTEGER NOT NULL DEFAULT 0,
 password_hash TEXT NOT NULL DEFAULT '', totp_secret TEXT NOT NULL DEFAULT '',
 revision INTEGER NOT NULL DEFAULT 0, last_step INTEGER NOT NULL DEFAULT -1,
 bootstrap_hash TEXT, bootstrap_expiry REAL NOT NULL DEFAULT 0,
 enrollment_secret TEXT, enrollment_expiry REAL NOT NULL DEFAULT 0,
 pending_secret TEXT, pending_expiry REAL NOT NULL DEFAULT 0, pending_session TEXT,
 recovery_required INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE recovery_codes (verifier TEXT PRIMARY KEY, consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessions (
 token_hash TEXT PRIMARY KEY, revision INTEGER NOT NULL, created REAL NOT NULL,
 last_activity REAL NOT NULL, absolute_expiry REAL NOT NULL, recent_auth REAL
);
CREATE TABLE auth_challenges (
 token_hash TEXT PRIMARY KEY, purpose TEXT NOT NULL, session_hash TEXT,
 revision INTEGER NOT NULL, created REAL NOT NULL, expiry REAL NOT NULL
);
CREATE TABLE otp_codes (
 challenge_hash TEXT PRIMARY KEY REFERENCES auth_challenges(token_hash) ON DELETE CASCADE,
 verifier TEXT NOT NULL, expiry REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 resend_after REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE auth_rate_limits (
 id INTEGER PRIMARY KEY, bucket TEXT NOT NULL, peer TEXT NOT NULL, reserved_at REAL NOT NULL
);
CREATE INDEX rate_window ON auth_rate_limits(bucket, reserved_at);
CREATE TABLE relay_settings (
 id INTEGER PRIMARY KEY CHECK(id=1), host TEXT NOT NULL, port INTEGER NOT NULL,
 username TEXT NOT NULL, credential TEXT NOT NULL, api_key TEXT NOT NULL,
 revision INTEGER NOT NULL, readiness_test TEXT
);
CREATE TABLE relay_tests (
 id TEXT PRIMARY KEY, revision INTEGER NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
 connection_ok INTEGER NOT NULL, accepted INTEGER NOT NULL, confirmed INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL
);
CREATE TABLE mailboxes (
 email TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
 created REAL NOT NULL, updated REAL NOT NULL, status TEXT NOT NULL, outbound_test TEXT
);
CREATE TABLE audit_logs (
 id INTEGER PRIMARY KEY, created REAL NOT NULL, action TEXT NOT NULL,
 outcome TEXT NOT NULL, request_id TEXT NOT NULL
);
CREATE INDEX audit_retention ON audit_logs(created);
PRAGMA user_version=1;
COMMIT;
"""
