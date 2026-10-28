"""Metadata never replaces Maddy inventories or stores recoverable mailbox passwords."""

import uuid
from collections.abc import Callable
from typing import Any

from backend.database import Database
from backend.helper_client import HelperAccess
from shared.errors import ServiceError
from shared.validation import address, bounded_text, mailbox_password, new_address, request_id


class Mailboxes:
    def __init__(self, dependencies: tuple[Database, HelperAccess, Callable[[], float]]) -> None:
        self.database, self.helper, self.clock = dependencies

    def inventory(self) -> list[dict[str, Any]]:
        observed = self.helper.call({"operation": "inventory", "request_id": str(uuid.uuid4())})
        credentials, storage = set(observed["credentials"]), set(observed["storage"])
        now = self.clock()
        with self.database.transaction() as connection:
            for email in sorted(credentials | storage):
                status = (
                    "ready"
                    if email in credentials and email in storage
                    else "reconciliation_required"
                )
                connection.execute(
                    "INSERT INTO mailboxes(email,created,updated,status) VALUES(?,?,?,?) "
                    "ON CONFLICT(email) DO UPDATE SET status=CASE WHEN mailboxes.status="
                    "'reconciliation_required' THEN mailboxes.status ELSE excluded.status END",
                    (email, now, now, status),
                )
            connection.execute(
                "UPDATE mailboxes SET created=? WHERE created IS NULL OR created<=0", (now,),
            )
            rows = {
                row["email"]: dict(row) for row in connection.execute("SELECT * FROM mailboxes")
            }
            confirmed_senders = {row[0] for row in connection.execute(
                "SELECT DISTINCT sender FROM relay_tests WHERE confirmed=1 "
                "AND revision=(SELECT revision FROM relay_settings WHERE id=1)"
            )}
            tests = {
                email: [dict(test) for test in connection.execute(
                    'SELECT id,accepted,confirmed,created FROM relay_tests '
                    'WHERE sender=? AND revision=(SELECT revision FROM relay_settings WHERE id=1) '
                    'ORDER BY created DESC LIMIT 5', (email,),
                )] for email in rows
            }
        return [
            {
                **rows[email],
                "credentials_present": email in credentials,
                "storage_present": email in storage,
                "outbound_readiness": ('confirmed' if email in confirmed_senders
                                       else 'awaiting receipt' if any(
                                           test['accepted'] for test in tests[email])
                                       else 'failed' if tests[email] else 'unverified'),
                'sender_tests': tests[email],
            }
            for email in sorted(
                credentials | storage, key=lambda email: (-rows[email]["created"], email),
            )
        ]

    def details(self, email: str) -> dict[str, Any]:
        normalized = address(email)
        for mailbox in self.inventory():
            if mailbox["email"] == normalized:
                with self.database.transaction() as connection:
                    revision = connection.execute(
                        "SELECT revision FROM relay_settings WHERE id=1"
                    ).fetchone()
                    mailbox["current_relay_revision"] = revision[0] if revision else 0
                    mailbox["sender_history"] = [dict(row) for row in connection.execute(
                        "SELECT id,revision,connection_ok,accepted,confirmed,created "
                        "FROM relay_tests WHERE sender=? ORDER BY created DESC LIMIT 20",
                        (normalized,),
                    )]
                return mailbox
        raise ServiceError("account_not_found", 404)

    def create(self, payload: dict[str, str]) -> dict[str, Any]:
        email = new_address(payload["email"])
        identifier = request_id(payload["request_id"])
        password = mailbox_password(payload["password"])
        display_name = bounded_text(payload.get("display_name", ""), 256)
        notes = bounded_text(payload.get("notes", ""), 2048)
        result = self.helper.call(
            {"operation": "create", "request_id": identifier, "email": email, "password": password}
        )
        now = self.clock()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    "INSERT INTO mailboxes(email,display_name,notes,created,updated,status) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET "
                    "display_name=CASE WHEN mailboxes.display_name='' "
                    "THEN excluded.display_name ELSE mailboxes.display_name END,"
                    "notes=CASE WHEN mailboxes.notes='' "
                    "THEN excluded.notes ELSE mailboxes.notes END,"
                    "status=CASE WHEN excluded.status='reconciliation_required' "
                    "THEN excluded.status ELSE mailboxes.status END",
                    (
                        email,
                        display_name,
                        notes,
                        now,
                        now,
                        "ready" if result["state"] == "complete" else "reconciliation_required",
                    ),
                )
                self._audit(connection, ("create", result["state"], identifier))
        except ServiceError as error:
            raise ServiceError("metadata_reconciliation_required", 503) from error
        return result

    def reset(self, email: str, payload: dict[str, str]) -> dict[str, Any]:
        result = self.helper.call(
            {
                "operation": "password",
                "request_id": request_id(payload["request_id"]),
                "email": address(email),
                "password": mailbox_password(payload["password"]),
            }
        )
        with self.database.transaction() as connection:
            self._audit(connection, ("mailbox-password", result["state"], payload["request_id"]))
        return result

    def delete(self, email: str, payload: dict[str, Any]) -> dict[str, Any]:
        email = address(email)
        if payload["confirmation"] != email or payload["acknowledged"] is not True:
            raise ServiceError("deletion_confirmation_required", 400)
        result = self.helper.call(
            {
                "operation": "delete",
                "request_id": request_id(payload["request_id"]),
                "email": email,
                "confirmation": email,
                "acknowledged": True,
                "cutoff": payload["cutoff"],
            }
        )
        with self.database.transaction() as connection:
            if result["state"] == "complete":
                connection.execute("DELETE FROM mailboxes WHERE email=?", (email,))
            else:
                connection.execute(
                    "UPDATE mailboxes SET status='reconciliation_required' WHERE email=?", (email,)
                )
            self._audit(connection, ("delete", result["state"], payload["request_id"]))
        return result

    def existing(self, email: str) -> str:
        normalized = address(email)
        rows = self.inventory()
        if not any(
            row["email"] == normalized and row["credentials_present"] and row["storage_present"]
            for row in rows
        ):
            raise ServiceError("account_not_found", 404)
        return normalized

    def update_metadata(self, email: str, payload: dict[str, str]) -> dict[str, str]:
        normalized = self.existing(email)
        name = bounded_text(payload['display_name'], 256)
        notes = bounded_text(payload['notes'], 2048)
        with self.database.transaction() as connection:
            connection.execute(
                'UPDATE mailboxes SET display_name=?,notes=?,updated=? WHERE email=?',
                (name, notes, self.clock(), normalized),
            )
            self._audit(connection, ('mailbox-metadata', 'complete', str(uuid.uuid4())))
        return {'email': normalized, 'display_name': name, 'notes': notes}

    def _audit(self, connection: Any, event: tuple[str, str, str]) -> None:
        connection.execute(
            "INSERT INTO audit_logs(created,action,outcome,request_id) VALUES(?,?,?,?)",
            (self.clock(), *event),
        )
