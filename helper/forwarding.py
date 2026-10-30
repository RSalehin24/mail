"""Root-owned forward-copy rules and durable message outcomes; local mail is never deleted."""

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import protected_json
from shared.installation import site
from shared.validation import address, request_id


def destination(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 254
        or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}",
            value,
        )
    ):
        raise ServiceError("invalid_forwarding_destination", 400)
    if value.rsplit("@", 1)[1].lower() == site().domain:
        raise ServiceError("forwarding_requires_external_destination", 400)
    return value


def validate_forwarding(payload: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "forwarding_list": set(),
        "forwarding_save": {"email", "destination", "enabled", "revision"},
        "forwarding_remove": {"email", "revision"},
        "forwarding_retry": {"email", "acknowledged"},
        "forwarding_verify": {"email"},
        "forwarding_verify_local": {"email"},
    }
    operation = payload["operation"]
    if operation not in fields or set(payload) != fields[operation] | {
        "operation",
        "protocol",
        "request_id",
    }:
        raise ServiceError("invalid_helper_fields", 400)
    request_id(payload["request_id"])
    if operation != "forwarding_list":
        address(payload["email"])
    if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] < 0):
        raise ServiceError("forwarding_revision_required", 400)
    if operation == "forwarding_save":
        destination(payload["destination"])
        if type(payload["enabled"]) is not bool:
            raise ServiceError("invalid_forwarding_rule", 400)
    if operation == "forwarding_retry" and payload["acknowledged"] is not True:
        raise ServiceError("forwarding_retry_acknowledgment_required", 400)
    return {k: v for k, v in payload.items() if k not in {"operation", "protocol", "request_id"}}


class ForwardingControl:
    def __init__(self, root: Path, data: Path) -> None:
        self.root, self.data = root, data
        self.path = root / "forwarding.db"
        if self.path.is_symlink() or (
            self.path.exists()
            and (self.path.stat().st_uid != root.stat().st_uid or self.path.stat().st_mode & 0o077)
        ):
            raise ServiceError("unsafe_forwarding_state", 503)
        with self.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS metadata (
                id INTEGER PRIMARY KEY,revision INTEGER NOT NULL);
              INSERT OR IGNORE INTO metadata VALUES(1,0);
              CREATE TABLE IF NOT EXISTS rules (email TEXT PRIMARY KEY,destination TEXT NOT NULL,
                enabled INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS messages (email TEXT NOT NULL,body_key TEXT NOT NULL,
                destination TEXT NOT NULL,state TEXT NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',body BLOB,
                PRIMARY KEY(email,body_key,destination));
              CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS verifications (
                destination TEXT PRIMARY KEY,state TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS audit (created REAL NOT NULL,action TEXT NOT NULL,
                email TEXT NOT NULL,request_id TEXT NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Any:
        db = sqlite3.connect(self.path, timeout=0.25)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def inbox(self, email: str) -> list[sqlite3.Row]:
        path = self.data / "imapsql.db"
        if path.is_symlink() or not path.is_file():
            raise ServiceError("mailbox_store_missing", 503)
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25) as db:
            db.row_factory = sqlite3.Row
            user = db.execute("SELECT id FROM users WHERE username=?", (email,)).fetchone()
            if user is None:
                raise ServiceError("account_not_found", 404)
            return db.execute(
                "SELECT DISTINCT e.id,m.date,m.compressAlgo FROM extKeys e "
                "JOIN msgs m ON m.extBodyKey=e.id JOIN mboxes b ON b.id=m.mboxId "
                "WHERE e.uid=? AND b.name='INBOX'",
                (user["id"],),
            ).fetchall()

    def listing(self) -> dict[str, Any]:
        with self.connect() as db:
            rules = []
            for row in db.execute("SELECT * FROM rules ORDER BY email"):
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                item["keep_local"] = True
                last = db.execute(
                    "SELECT state,error,updated FROM messages WHERE email=? "
                    "AND state NOT IN ('baseline','skipped') ORDER BY updated DESC LIMIT 1",
                    (item["email"],),
                ).fetchone()
                item["last_result"] = dict(last) if last else None
                item["pending"] = db.execute(
                    "SELECT count(*) FROM messages WHERE email=? AND state='queued'",
                    (item["email"],),
                ).fetchone()[0]
                item["attention"] = db.execute(
                    "SELECT count(*) FROM messages WHERE email=? "
                    "AND state IN ('failed','uncertain')",
                    (item["email"],),
                ).fetchone()[0]
                rules.append(item)
            path = self.root / "cloudflare-forwarding.json"
            cloudflare: dict[str, Any] = (
                protected_json(path, self.root.stat().st_uid)
                if path.exists()
                else {
                    "configured": False,
                    "state": "not_connected",
                    "observed_at": None,
                    "reason": "cloudflare_credentials_required",
                }
            )
            revision = db.execute("SELECT revision FROM metadata WHERE id=1").fetchone()[0]
            if cloudflare.get("configured") and cloudflare.get("revision") != revision:
                cloudflare = {**cloudflare, "state": "pending"}
            elif (
                cloudflare.get("state") == "connected"
                and time.time() - float(cloudflare.get("observed_at") or 0) > 300
            ):
                cloudflare = {**cloudflare, "state": "stale"}
            for item in rules:
                state = cloudflare.get("mailboxes", {}).get(item["email"], {})
                item["local_delivery_verified"] = bool(state.get("local_delivery_verified"))
                item["destination_verified"] = item["destination"] in cloudflare.get(
                    "verified_destinations", []
                )
                row = db.execute(
                    "SELECT state FROM verifications WHERE destination=?", (item["destination"],)
                ).fetchone()
                item["verification_state"] = (
                    "verified"
                    if item["destination_verified"]
                    else row[0]
                    if row
                    else "not_requested"
                )
                item["synchronized"] = cloudflare.get("revision") == revision and item[
                    "email"
                ] in cloudflare.get("connected_mailboxes", [])
            return {
                "connection": cloudflare,
                "revision": db.execute("SELECT revision FROM metadata WHERE id=1").fetchone()[0],
                "rules": rules,
                "mode": "transparent",
                "keep_local": True,
                "worker": protected_json(
                    self.root / "forwarding-worker-status.json", self.root.stat().st_uid
                )
                if (self.root / "forwarding-worker-status.json").exists()
                else None,
            }

    def handle(
        self, operation: str, identifier: str, values: dict[str, Any], now: float
    ) -> dict[str, Any]:
        if operation == "forwarding_list":
            return self.listing()
        encoded = json.dumps({"operation": operation, **values}, sort_keys=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE id=?", (identifier,)).fetchone()
            if old:
                if old["payload"] != encoded:
                    raise ServiceError("request_id_conflict", 409)
                return self.listing()
            email = address(values["email"])
            if (
                "revision" in values
                and values["revision"]
                != db.execute("SELECT revision FROM metadata WHERE id=1").fetchone()[0]
            ):
                raise ServiceError("forwarding_settings_changed", 409)
            if operation == "forwarding_save":
                recipient = destination(values["destination"])
                existing = db.execute("SELECT * FROM rules WHERE email=?", (email,)).fetchone()
                rows = self.inbox(email)
                changed = (
                    existing is None
                    or existing["destination"] != recipient
                    or not existing["enabled"]
                )
                if changed:
                    db.execute(
                        "UPDATE messages SET state='cancelled',body=NULL,updated=? "
                        "WHERE email=? AND state='queued'",
                        (now, email),
                    )
                    db.executemany(
                        "INSERT OR IGNORE INTO messages"
                        "(email,body_key,destination,state,created,updated) "
                        "VALUES(?,?,?,'baseline',?,?)",
                        [(email, row["id"], recipient, now, now) for row in rows],
                    )
                db.execute(
                    "INSERT INTO rules VALUES(?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET "
                    "destination=excluded.destination,enabled=excluded.enabled,created=excluded.created,updated=excluded.updated",
                    (
                        email,
                        recipient,
                        int(values["enabled"]),
                        now if changed else existing["created"],
                        now,
                    ),
                )
                if not values["enabled"]:
                    db.execute(
                        "UPDATE messages SET state='cancelled',body=NULL,updated=? "
                        "WHERE email=? AND state='queued'",
                        (now, email),
                    )
            elif operation == "forwarding_remove":
                db.execute("DELETE FROM rules WHERE email=?", (email,))
                db.execute(
                    "UPDATE messages SET state='cancelled',body=NULL,updated=? "
                    "WHERE email=? AND state='queued'",
                    (now, email),
                )
            elif operation == "forwarding_verify_local":
                self.inbox(email)
                recipient = email.split("@", 1)[0] + "@" + site().delivery_domain
                db.execute(
                    "INSERT INTO verifications VALUES(?,'queued') ON CONFLICT(destination) "
                    "DO UPDATE SET state='queued'",
                    (recipient,),
                )
            elif operation == "forwarding_verify":
                rule = db.execute(
                    "SELECT destination FROM rules WHERE email=?", (email,)
                ).fetchone()
                if rule is None:
                    raise ServiceError("forwarding_rule_missing", 404)
                db.execute(
                    "INSERT INTO verifications VALUES(?,'queued') ON CONFLICT(destination) "
                    "DO UPDATE SET state='queued'",
                    (rule["destination"],),
                )
            elif operation == "forwarding_retry":
                rule = db.execute(
                    "SELECT * FROM rules WHERE email=? AND enabled=1", (email,)
                ).fetchone()
                if rule is None:
                    raise ServiceError("forwarding_rule_disabled", 409)
                db.execute(
                    "UPDATE messages SET state='queued',error='',updated=? WHERE email=? "
                    "AND destination=? AND state IN ('failed','uncertain') AND body IS NOT NULL",
                    (now, email, rule["destination"]),
                )
            db.execute("UPDATE metadata SET revision=revision+1 WHERE id=1")
            db.execute("INSERT INTO audit VALUES(?,?,?,?)", (now, operation, email, identifier))
            result = {"saved": True, "email": email}
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)", (identifier, encoded, json.dumps(result))
            )
        return self.listing()
