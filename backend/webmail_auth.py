"""Mailbox authentication and a process-local encrypted credential vault."""

import base64
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any

import pyotp
import qrcode
import qrcode.image.svg

from backend.auth import Authentication
from backend.factors import matching_step
from backend.secrets import SecretKeys
from backend.webmail_imap import ImapTransport, MailConnection
from backend.webmail_models import FactorChange, ProfileChange, SecurityProof
from shared.errors import ServiceError
from shared.installation import site
from shared.validation import address


@dataclass(frozen=True)
class Identity:
    id: str
    email: str
    validity: int
    revision: int
    token_hash: str
    authority_revision: int = 0


@dataclass
class CachedConnection:
    manager: AbstractContextManager[MailConnection]
    mail: MailConnection
    last_used: float


@dataclass
class Ownership:
    condition: threading.Condition = field(default_factory=threading.Condition)
    readers: int = 0
    writing: bool = False
    waiting_writers: int = 0


class MailboxAuthentication:
    def __init__(self, admin: Authentication, imap: ImapTransport, authority: Any = None) -> None:
        self.database, self.keys = admin.database, admin.keys
        self.clock: Callable[[], float] = admin.dependencies.clock
        self.imap = imap
        self.authority = authority
        self.trash: Any = None
        self.ephemeral = SecretKeys(secrets.token_bytes(32), secrets.token_bytes(32))
        self.vault: dict[str, tuple[str, float]] = {}
        self.challenges: dict[str, tuple[str, str, int, float]] = {}
        self.lock = threading.RLock()
        self.connections: dict[str, CachedConnection] = {}
        self.connection_locks: dict[str, threading.Lock] = {}
        self.activity: dict[str, float] = {}
        self.next_cleanup = 0.0
        self.owner_locks: dict[str, Ownership] = {}
        self.owner_versions: dict[str, int] = {}
        # Persisted sessions intentionally cannot survive loss of the vault key.
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM mailbox_sessions")
            connection.execute(
                "UPDATE mailbox_sends SET state='submission_uncertain' WHERE state='submitting'"
            )
            connection.execute(
                "UPDATE mailbox_sends SET state='preparation_uncertain' WHERE state='preparing'"
            )
            connection.execute(
                "UPDATE mailbox_sends SET state='sent_save_uncertain' WHERE state='saving_sent'"
            )

    def rate(self, peer: str) -> None:
        now = self.clock()
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT peer FROM auth_rate_limits WHERE bucket='webmail-auth' AND reserved_at>?",
                (now - 900,),
            ).fetchall()
            if len(rows) >= 120 or sum(row[0] == peer for row in rows) >= 30:
                raise ServiceError("rate_limited", 429, 60)
            connection.execute(
                "INSERT INTO auth_rate_limits(bucket,peer,reserved_at) VALUES(?,?,?)",
                ("webmail-auth", peer, now),
            )

    def cleanup(self, force: bool = False) -> None:
        now = self.clock()
        with self.lock:
            for key, (_, expiry) in list(self.vault.items()):
                if expiry <= now or self.activity.get(key, now) + 900 <= now:
                    self.vault.pop(key)
                    self.activity.pop(key, None)
                    cached = self.connections.pop(key, None)
                    if cached is not None:
                        cached.manager.__exit__(None, None, None)
            if not force and now < self.next_cleanup:
                return
            self.next_cleanup = now + 60
        with self.lock, self.database.transaction() as connection:
            connection.executemany(
                "UPDATE mailbox_sessions SET last_activity=? WHERE token_hash=?",
                [(seen, key) for key, seen in self.activity.items()],
            )
            connection.execute(
                "DELETE FROM mailbox_sessions WHERE absolute_expiry<=? OR last_activity<=?",
                (now, now - 900),
            )
            valid = {
                row[0] for row in connection.execute("SELECT token_hash FROM mailbox_sessions")
            }
            for key in list(self.vault):
                if self.vault[key][1] <= now or key not in valid:
                    del self.vault[key]
                    self.activity.pop(key, None)
            for key, cached in list(self.connections.items()):
                if key not in self.vault or cached.last_used + 120 <= now:
                    self.connections.pop(key).manager.__exit__(None, None, None)
            for key in list(self.connection_locks):
                if key not in self.vault:
                    del self.connection_locks[key]
            self.challenges = {
                key: value for key, value in self.challenges.items() if value[3] > now
            }

    @contextmanager
    def owner_guard(self, email: str, wait: bool = False) -> Iterator[None]:
        with self.lock:
            owner = self.owner_locks.setdefault(email.lower(), Ownership())
        with owner.condition:
            if wait:
                owner.waiting_writers += 1
                try:
                    if not owner.condition.wait_for(
                        lambda: not owner.writing and owner.readers == 0, timeout=10
                    ):
                        raise ServiceError("mailbox_busy", 503, 2)
                    owner.writing = True
                finally:
                    owner.waiting_writers -= 1
            else:
                if owner.writing or owner.waiting_writers:
                    raise ServiceError("mailbox_busy", 503, 2)
                owner.readers += 1
        if wait:
            with self.lock:
                self.owner_versions[email.lower()] = self.owner_versions.get(email.lower(), 0) + 1
        try:
            yield
        finally:
            if wait:
                with self.lock:
                    self.owner_versions[email.lower()] += 1
            with owner.condition:
                if wait:
                    owner.writing = False
                else:
                    owner.readers -= 1
                owner.condition.notify_all()

    def login(self, email: str, password: str, peer: str) -> tuple[dict[str, Any], str | None]:
        with self.lock:
            version = self.owner_versions.get(email.lower(), 0)
        return self._login(email, password, peer, version)

    def _login(
        self, email: str, password: str, peer: str, version: int
    ) -> tuple[dict[str, Any], str | None]:
        self.rate(peer)
        self.cleanup()
        try:
            email = address(email)
        except ServiceError as error:
            raise ServiceError("authentication_failed", 401) from error
        if not password or len(password.encode()) > 1024 or any(c in password for c in "\r\n\0"):
            raise ServiceError("authentication_failed", 401)
        remote = self.authority.identify(email) if self.authority is not None else None
        try:
            with self.imap.connect(email, password) as mail:
                validity = mail.select("INBOX")
        except ServiceError as error:
            if error.status == 401:
                raise ServiceError("authentication_failed", 401) from error
            raise
        if remote is not None:
            if self.authority.identify(email) != remote or remote["inbox_validity"] != validity:
                raise ServiceError("mailbox_security_revision_changed", 409)
        with self.owner_guard(email), self.lock, self.database.transaction() as connection:
            if version != self.owner_versions.get(email, 0):
                raise ServiceError("mailbox_changed_refresh", 409)
            row = connection.execute(
                "SELECT * FROM mailbox_users WHERE email=? AND active=1",
                (email,),
            ).fetchone()
            if remote is not None and row is not None and row["id"] != remote["id"]:
                connection.execute("UPDATE mailbox_users SET active=0 WHERE id=?", (row["id"],))
                connection.execute("DELETE FROM mailbox_sessions WHERE user_id=?", (row["id"],))
                row = None
            if (
                remote is not None
                and row is not None
                and row["authority_revision"] != remote["revision"]
            ):
                connection.execute(
                    "UPDATE mailbox_users SET authority_revision=?,revision=revision+1 WHERE id=?",
                    (remote["revision"], row["id"]),
                )
                connection.execute("DELETE FROM mailbox_sessions WHERE user_id=?", (row["id"],))
                row = connection.execute(
                    "SELECT * FROM mailbox_users WHERE id=?", (row["id"],)
                ).fetchone()
            if remote is not None and row is not None and row["inbox_validity"] != validity:
                # Management only retains this ID after an explicit same-owner reconciliation.
                connection.execute(
                    "UPDATE mailbox_users SET inbox_validity=?,revision=revision+1 WHERE id=?",
                    (validity, row["id"]),
                )
                connection.execute("DELETE FROM mailbox_sessions WHERE user_id=?", (row["id"],))
                row = connection.execute(
                    "SELECT * FROM mailbox_users WHERE id=?",
                    (row["id"],),
                ).fetchone()
            if row is not None and row["inbox_validity"] != validity:
                # A folder generation change cannot authorize resetting a profile
                # or its authenticator. Managed account deletion retires identity.
                raise ServiceError("mailbox_identity_reconciliation_required", 409)
            if row is None:
                identifier = remote["id"] if remote is not None else secrets.token_hex(16)
                connection.execute(
                    "INSERT INTO mailbox_users(id,email,inbox_validity,created) VALUES(?,?,?,?)",
                    (identifier, email, validity, self.clock()),
                )
                if remote is not None:
                    connection.execute(
                        "UPDATE mailbox_users SET authority_revision=? WHERE id=?",
                        (remote["revision"], identifier),
                    )
                row = connection.execute(
                    "SELECT * FROM mailbox_users WHERE id=?", (identifier,)
                ).fetchone()
            if row["totp_secret"]:
                if len(self.challenges) >= 100:
                    raise ServiceError("webmail_busy", 503, 2)
                challenge = "wm." + secrets.token_urlsafe(32)
                hashed = self.keys.digest(challenge, "mailbox-challenge")
                self.challenges[hashed] = (
                    row["id"],
                    self.ephemeral.seal(password, hashed),
                    row["revision"],
                    self.clock() + 300,
                )
                return {
                    "challenge": challenge,
                    "email_otp_available": False,
                    "role": "mailbox",
                }, None
            return self._issue(connection, row, password)

    def _issue(
        self, connection: sqlite3.Connection, row: sqlite3.Row, password: str
    ) -> tuple[dict[str, Any], str]:
        token = "wm." + secrets.token_urlsafe(32)
        hashed = self.keys.digest(token, "mailbox-session")
        now = self.clock()
        connection.execute(
            "INSERT INTO mailbox_sessions VALUES(?,?,?,?,?,?)",
            (hashed, row["id"], row["revision"], now, now, now + 43200),
        )
        self.vault[hashed] = (self.ephemeral.seal(password, hashed), now + 43200)
        self.activity[hashed] = now
        return {
            "authenticated": True,
            "role": "mailbox",
            "user": {"id": row["id"], "email": row["email"]},
        }, token

    def verify(
        self, challenge: str, factor: str, code: str, peer: str
    ) -> tuple[dict[str, Any], str]:
        self.rate(peer)
        self.cleanup()
        hashed = self.keys.digest(challenge, "mailbox-challenge")
        with self.lock, self.database.transaction() as connection:
            pending = self.challenges.get(hashed)
            if pending is None:
                raise ServiceError("authentication_failed", 401)
            row = connection.execute(
                "SELECT * FROM mailbox_users WHERE id=? AND active=1 AND revision=?",
                (pending[0], pending[2]),
            ).fetchone()
            if row is None or not self._factor(connection, row, factor, code):
                raise ServiceError("authentication_failed", 401)
            password = self.ephemeral.open(pending[1], hashed)
            result = self._issue(connection, row, password)
            del self.challenges[hashed]
            return result

    def _factor(
        self, connection: sqlite3.Connection, row: sqlite3.Row, factor: str, code: str
    ) -> bool:
        if not row["totp_secret"]:
            return True
        if factor == "recovery":
            return (
                connection.execute(
                    "DELETE FROM mailbox_recovery_codes WHERE user_id=? AND verifier=?",
                    (row["id"], self.keys.digest(code, "mailbox-recovery:" + row["id"])),
                ).rowcount
                == 1
            )
        if factor == "totp":
            secret = self.keys.open(row["totp_secret"], "mailbox-totp:" + row["id"])
            step = matching_step(secret, code, self.clock())
            if step is not None and step > row["last_step"]:
                connection.execute(
                    "UPDATE mailbox_users SET last_step=? WHERE id=?", (step, row["id"])
                )
                return True
        return False

    def require(self, token: str) -> Identity:
        self.cleanup()
        hashed = self.keys.digest(token, "mailbox-session")
        with self.lock, self.database.transaction() as connection:
            row = connection.execute(
                "SELECT s.*,u.email,u.inbox_validity,u.id AS owner FROM mailbox_sessions s "
                "JOIN mailbox_users u ON u.id=s.user_id AND u.active=1 AND u.revision=s.revision "
                "WHERE s.token_hash=?",
                (hashed,),
            ).fetchone()
            if (
                row is None
                or hashed not in self.vault
                or row["absolute_expiry"] <= self.clock()
                or max(row["last_activity"], self.activity.get(hashed, 0)) + 900 <= self.clock()
            ):
                self.vault.pop(hashed, None)
                self.activity.pop(hashed, None)
                connection.execute("DELETE FROM mailbox_sessions WHERE token_hash=?", (hashed,))
                cached = self.connections.pop(hashed, None)
                if cached is not None:
                    cached.manager.__exit__(None, None, None)
                raise ServiceError("authentication_required", 401)
            self.activity[hashed] = self.clock()
            expected = 0
            if self.authority is not None:
                expected = connection.execute(
                    "SELECT authority_revision FROM mailbox_users WHERE id=?",
                    (row["user_id"],),
                ).fetchone()[0]
            identity = Identity(
                row["user_id"],
                row["email"],
                row["inbox_validity"],
                row["revision"],
                hashed,
                expected,
            )
        if self.authority is not None:
            try:
                remote = self.authority.identify(identity.email)
            except ServiceError as error:
                if error.status in {401, 404, 409}:
                    self.logout(token)
                raise
            if (
                remote["id"] != identity.id
                or remote["revision"] != expected
                or remote["inbox_validity"] != identity.validity
            ):
                self.logout(token)
                raise ServiceError("authentication_required", 401)
        return identity

    @contextmanager
    def connect(self, token: str) -> Iterator[tuple[Identity, MailConnection]]:
        identity = self.require(token)
        with self.owner_guard(identity.email), self._connect(token, identity) as connected:
            yield connected
            self.require(token)

    @contextmanager
    def _connect(self, token: str, identity: Identity) -> Iterator[tuple[Identity, MailConnection]]:
        with self.lock:
            mutex = self.connection_locks.setdefault(identity.token_hash, threading.Lock())
        if not mutex.acquire(timeout=2):
            raise ServiceError("webmail_busy", 503, 2)
        cached, usable = None, False
        try:
            with self.lock:
                cached = self.connections.pop(identity.token_hash, None)
                sealed = self.vault.get(identity.token_hash)
                if sealed is None:
                    raise ServiceError("authentication_required", 401)
            if cached is None:
                password = self.ephemeral.open(sealed[0], identity.token_hash)
                manager = self.imap.connect(identity.email, password)
                mail = manager.__enter__()
                password = ""
                cached = CachedConnection(manager, mail, self.clock())
            else:
                cached.mail.deadline = time.monotonic() + 9
            cached.mail.select("INBOX", identity.validity)
            self.require(token)
            usable = True
            yield identity, cached.mail
        except ServiceError as error:
            usable = usable and error.code not in {
                "mail_connection_lost",
                "mail_timeout",
                "mail_authentication_failed",
            }
            raise
        except BaseException:
            usable = False
            raise
        finally:
            with self.lock:
                if cached is not None:
                    if usable and identity.token_hash in self.vault:
                        cached.last_used = self.clock()
                        self.connections[identity.token_hash] = cached
                        # At most ten idle connections, plus the three admitted
                        # active operations. Connections are never shared by users.
                        while len(self.connections) > 10:
                            oldest = next(iter(self.connections))
                            self.connections.pop(oldest).manager.__exit__(None, None, None)
                    else:
                        cached.manager.__exit__(None, None, None)
            mutex.release()

    def logout(self, token: str) -> None:
        hashed = self.keys.digest(token, "mailbox-session")
        with self.lock, self.database.transaction() as connection:
            connection.execute("DELETE FROM mailbox_sessions WHERE token_hash=?", (hashed,))
            self.vault.pop(hashed, None)
            self.activity.pop(hashed, None)
            cached = self.connections.pop(hashed, None)
            if cached is not None:
                cached.manager.__exit__(None, None, None)

    def profile(self, token: str) -> dict[str, Any]:
        identity = self.require(token)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mailbox_users WHERE id=?", (identity.id,)
            ).fetchone()
            return {
                **{
                    name: row[name]
                    for name in ("id", "email", "display_name", "reply_to", "signature", "theme")
                },
                "two_step_enabled": bool(row["totp_secret"]),
                "recovery_codes_remaining": connection.execute(
                    "SELECT count(*) FROM mailbox_recovery_codes WHERE user_id=?",
                    (identity.id,),
                ).fetchone()[0],
                "connections": {
                    "host": site().mail_hostname,
                    "imap_port": 993,
                    "imap_security": "TLS",
                    "smtp_port": 587,
                    "smtp_security": "STARTTLS",
                    "username": identity.email,
                },
            }

    def save_profile(self, token: str, body: ProfileChange) -> dict[str, Any]:
        from backend.webmail_mime import email_address, header

        identity = self.require(token)
        header(body.display_name)
        if body.reply_to:
            email_address(body.reply_to)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_users SET display_name=?,reply_to=?,signature=?,theme=? WHERE id=?",
                (body.display_name, body.reply_to, body.signature, body.theme, identity.id),
            )
        return self.profile(token)

    def prove_password(self, token: str, body: SecurityProof, peer: str) -> Identity:
        self.rate(peer)
        identity = self.require(token)
        with self.imap.connect(identity.email, body.password) as mail:
            mail.select("INBOX", identity.validity)
        return self.require(token)

    def consume_proof(
        self, connection: sqlite3.Connection, identity: Identity, body: SecurityProof
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM mailbox_users WHERE id=? AND active=1 AND revision=?",
            (identity.id, identity.revision),
        ).fetchone()
        if row is None or not self._factor(connection, row, body.factor, body.code):
            raise ServiceError("authentication_failed", 401)
        return row  # type: ignore[no-any-return]

    def factor_change(self, token: str, body: FactorChange, peer: str) -> dict[str, Any]:
        identity = self.prove_password(token, body, peer)
        with self.lock, self.database.transaction() as connection:
            row = self.consume_proof(connection, identity, body)
            if body.action == "begin":
                secret = pyotp.random_base32()
                connection.execute(
                    "UPDATE mailbox_users SET pending_secret=?,pending_expiry=?,"
                    "pending_session=? WHERE id=?",
                    (
                        self.keys.seal(secret, "mailbox-pending:" + identity.id),
                        self.clock() + 300,
                        identity.token_hash,
                        identity.id,
                    ),
                )
                uri = pyotp.TOTP(secret).provisioning_uri(identity.email, issuer_name="Webmail")
                qr = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage)
                return {
                    "secret": secret,
                    "uri": uri,
                    "qr": "data:image/svg+xml;base64," + base64.b64encode(qr.to_string()).decode(),
                }
            if body.action == "confirm":
                if (
                    not row["pending_secret"]
                    or row["pending_expiry"] <= self.clock()
                    or row["pending_session"] != identity.token_hash
                ):
                    raise ServiceError("enrollment_expired", 409)
                secret = self.keys.open(row["pending_secret"], "mailbox-pending:" + identity.id)
                step = matching_step(secret, body.new_code, self.clock())
                if step is None:
                    raise ServiceError("authentication_failed", 401)
                connection.execute(
                    "UPDATE mailbox_users SET totp_secret=?,last_step=? WHERE id=?",
                    (self.keys.seal(secret, "mailbox-totp:" + identity.id), step, identity.id),
                )
            if body.action == "disable":
                connection.execute(
                    "UPDATE mailbox_users SET totp_secret='',last_step=-1 WHERE id=?",
                    (identity.id,),
                )
            elif body.action == "recovery" and not row["totp_secret"]:
                raise ServiceError("authenticator_required", 409)
            codes = [] if body.action == "disable" else [secrets.token_hex(16) for _ in range(5)]
            connection.execute("DELETE FROM mailbox_recovery_codes WHERE user_id=?", (identity.id,))
            connection.executemany(
                "INSERT INTO mailbox_recovery_codes VALUES(?,?)",
                [
                    (identity.id, self.keys.digest(c, "mailbox-recovery:" + identity.id))
                    for c in codes
                ],
            )
            connection.execute(
                "UPDATE mailbox_users SET pending_secret=NULL,revision=revision+1 WHERE id=?",
                (identity.id,),
            )
            connection.execute("DELETE FROM mailbox_sessions WHERE user_id=?", (identity.id,))
        self.cleanup(force=True)
        return {"recovery_codes": codes, "revoked": True}

    def close(self) -> None:
        with self.lock:
            for cached in self.connections.values():
                cached.manager.__exit__(None, None, None)
            self.connections.clear()
            self.connection_locks.clear()
            self.vault.clear()
            self.activity.clear()
            self.challenges.clear()
        close = getattr(self.authority, "close", None)
        if close is not None:
            close()
