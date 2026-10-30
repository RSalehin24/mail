"""Durable Trash coordination; a lost event or interrupted move leaves cleanup held."""

import json
import time
import uuid
from typing import Any, Literal, cast

from pydantic import Field

from applications.client import ManagementClient
from applications.identity import Identities
from backend.database import Database
from backend.requests import RequestModel
from backend.webmail_models import MessageRef
from shared.errors import ServiceError

SCHEMA = """
CREATE TABLE IF NOT EXISTS trash_holds (
 installation_id TEXT NOT NULL, id TEXT NOT NULL, mailbox_id TEXT NOT NULL,
 created REAL NOT NULL, PRIMARY KEY(installation_id,id)
);
CREATE TABLE IF NOT EXISTS trash_peers (
 installation_id TEXT PRIMARY KEY, seen REAL NOT NULL, recovery_epoch TEXT NOT NULL
);
"""


class TrashEvent(RequestModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    mailbox_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: Literal["hold", "ready", "entered", "restored", "delete"]
    hold_id: str = Field(default="", max_length=32)
    ref: MessageRef | None = None


class TrashBatch(RequestModel):
    recovery_epoch: str = Field(max_length=36)
    events: list[TrashEvent] = Field(max_length=50)


class TrashAuthority:
    def __init__(self, database: Database, identities: Identities) -> None:
        self.database, self.identities = database, identities
        with database.transaction() as connection:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)

    def receive(self, installation: str, batch: TrashBatch) -> dict[str, Any]:
        now = time.time()
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT recovery_epoch FROM trash_peers WHERE installation_id=?",
                (installation,),
            ).fetchone()
            if previous is not None and previous[0] != batch.recovery_epoch:
                connection.execute("UPDATE application_state SET cleanup_enabled=0")
            connection.execute(
                "INSERT OR REPLACE INTO trash_peers VALUES(?,?,?)",
                (installation, now, batch.recovery_epoch),
            )
            acknowledged = []
            for event in batch.events:
                payload = event.model_dump_json()
                known = connection.execute(
                    "SELECT payload FROM integration_trash_events WHERE installation_id=? AND id=?",
                    (installation, event.id),
                ).fetchone()
                if known is not None:
                    if known[0] != payload:
                        raise ServiceError("trash_event_conflict", 409)
                    acknowledged.append(event.id)
                    continue
                owner = connection.execute(
                    "SELECT id FROM managed_mailbox_identities WHERE id=? AND state='active'",
                    (event.mailbox_id,),
                ).fetchone()
                if owner is None:
                    raise ServiceError("trash_identity_reconciliation_required", 409)
                connection.execute(
                    "INSERT INTO integration_trash_events VALUES(?,?,?,?,?)",
                    (installation, event.id, event.mailbox_id, payload, now),
                )
                if event.action == "hold":
                    connection.execute(
                        "INSERT INTO trash_holds VALUES(?,?,?,?)",
                        (installation, event.id, event.mailbox_id, now),
                    )
                elif event.action == "ready":
                    connection.execute(
                        "DELETE FROM trash_holds WHERE installation_id=? AND id=? AND mailbox_id=?",
                        (installation, event.hold_id, event.mailbox_id),
                    )
                elif event.ref is not None:
                    key = (event.mailbox_id, event.ref.folder, event.ref.uidvalidity, event.ref.uid)
                    if event.action == "restored":
                        connection.execute(
                            "DELETE FROM managed_trash WHERE mailbox_id=? AND folder=? "
                            "AND uidvalidity=? AND uid=?",
                            key,
                        )
                    else:
                        connection.execute(
                            "INSERT INTO managed_trash VALUES(?,?,?,?,?,?,?) "
                            "ON CONFLICT(mailbox_id,folder,uidvalidity,uid) DO UPDATE SET "
                            "acknowledged=excluded.acknowledged,confirmed=max(confirmed,excluded.confirmed)",
                            (*key, now, now, int(event.action == "delete")),
                        )
                else:
                    raise ServiceError("trash_reference_required", 400)
                acknowledged.append(event.id)
        return {"acknowledged": acknowledged}

    def due(self, observed: dict[str, dict[str, Any]], now: float) -> list[dict[str, Any]]:
        """Native observer owns times and matches account and folder generations."""
        with self.database.transaction() as connection:
            enabled = connection.execute(
                "SELECT cleanup_enabled FROM application_state WHERE id=1"
            ).fetchone()[0]
            credentials = connection.execute(
                "SELECT installation_id FROM integration_credentials "
                "WHERE revoked=0 AND activated=1 "
                "AND expires>?",
                (now,),
            ).fetchall()
            fresh = bool(credentials) and all(
                connection.execute(
                    "SELECT 1 FROM trash_peers WHERE installation_id=? AND seen>?",
                    (row[0], now - 120),
                ).fetchone()
                for row in credentials
            )
            due = []
            for user in connection.execute(
                "SELECT * FROM managed_mailbox_identities WHERE state='active'"
            ).fetchall():
                native = observed.get(user["email"])
                if (
                    native is None
                    or native["native_id"] != user["native_id"]
                    or native["inbox_validity"] != user["inbox_validity"]
                ):
                    continue
                held = connection.execute(
                    "SELECT 1 FROM trash_holds WHERE mailbox_id=?",
                    (user["id"],),
                ).fetchone()
                for row in connection.execute(
                    "SELECT * FROM managed_trash WHERE mailbox_id=?",
                    (user["id"],),
                ).fetchall():
                    key = (row["folder"], row["uidvalidity"], row["uid"])
                    if key not in native["entries"]:
                        connection.execute(
                            "DELETE FROM managed_trash WHERE mailbox_id=? AND folder=? "
                            "AND uidvalidity=? AND uid=?",
                            (user["id"], *key),
                        )
                    elif (
                        enabled
                        and fresh
                        and not held
                        and (row["confirmed"] or row["entered"] <= now - 30 * 86400)
                    ):
                        due.append(
                            {
                                **dict(row),
                                "user_id": user["id"],
                                "email": user["email"],
                                "native_id": user["native_id"],
                                "inbox_validity": user["inbox_validity"],
                            }
                        )
                for key in native["entries"]:
                    connection.execute(
                        "INSERT OR IGNORE INTO managed_trash VALUES(?,?,?,?,?,?,0)",
                        (user["id"], *key, now, now),
                    )
        return due[:100]


class TrashOutbox:
    def __init__(self, database: Database, client: ManagementClient) -> None:
        self.database, self.client = database, client

    def enqueue(self, user: str, action: str, ref: MessageRef | None = None, hold: str = "") -> str:
        identifier = uuid.uuid4().hex
        payload = TrashEvent(
            id=identifier, mailbox_id=user, action=cast(Any, action), ref=ref, hold_id=hold
        ).model_dump_json()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO trash_outbox(id,mailbox_id,payload) VALUES(?,?,?)",
                (identifier, user, payload),
            )
        return identifier

    def flush(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute(
                "SELECT id,payload FROM trash_outbox WHERE acknowledged=0 "
                "ORDER BY sequence LIMIT 50"
            ).fetchall()
            epoch = connection.execute(
                "SELECT recovery_epoch FROM application_state WHERE id=1"
            ).fetchone()[0]
        result = self.client.call(
            "trash",
            {
                "recovery_epoch": epoch,
                "events": [json.loads(row["payload"]) for row in rows],
            },
        )
        expected = [row["id"] for row in rows]
        if result.get("acknowledged") != expected:
            raise ServiceError("trash_acknowledgment_missing", 503)
        with self.database.transaction() as connection:
            connection.executemany(
                "UPDATE trash_outbox SET acknowledged=1 WHERE id=?",
                [(key,) for key in expected],
            )

    def begin(self, user: str) -> str:
        identifier = self.enqueue(user, "hold")
        # Refuse a move until the server durably holds cleanup for this operation.
        for _ in range(20):
            self.flush()
            with self.database.transaction() as connection:
                if connection.execute(
                    "SELECT acknowledged FROM trash_outbox WHERE id=?",
                    (identifier,),
                ).fetchone()[0]:
                    return identifier
        raise ServiceError("trash_coordination_backlog", 503)
