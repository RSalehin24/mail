"""Management owns stable mailbox IDs; unavailable native checks never authorize access."""

import threading
import time
import uuid
from typing import Any

from backend.database import Database
from backend.helper_client import HelperAccess
from shared.errors import ServiceError
from shared.validation import address


class Identities:
    def __init__(self, database: Database, helper: HelperAccess) -> None:
        self.database, self.helper = database, helper
        self.locks: dict[str, Any] = {}
        self.lock = threading.Lock()

    def guard(self, email: str) -> Any:
        with self.lock:
            return self.locks.setdefault(address(email), threading.RLock())

    def lookup(self, email: str) -> dict[str, Any]:
        email = address(email)
        observed = self.helper.call(
            {
                "operation": "mailbox_identity",
                "request_id": str(uuid.uuid4()),
                "email": email,
            }
        )
        native, validity = observed.get("native_id"), observed.get("inbox_validity")
        fingerprint = observed.get("credential_fingerprint", "")
        if type(native) is not int or type(validity) is not int or native < 1 or validity < 1:
            raise ServiceError("mailbox_identity_unavailable", 503)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM managed_mailbox_identities WHERE email=? AND state!='retired'",
                (email,),
            ).fetchone()
            if row is not None and row["state"] != "active":
                raise ServiceError("mailbox_security_change_pending", 409)
            if row is not None and row["native_id"] != native:
                connection.execute(
                    "UPDATE managed_mailbox_identities SET state='retired',revision=revision+1 "
                    "WHERE id=?",
                    (row["id"],),
                )
                row = None
            if row is None:
                identifier = uuid.uuid4().hex
                connection.execute(
                    "INSERT INTO managed_mailbox_identities"
                    "(id,email,native_id,inbox_validity,created) VALUES(?,?,?,?,?)",
                    (identifier, email, native, validity, time.time()),
                )
                row = connection.execute(
                    "SELECT * FROM managed_mailbox_identities WHERE id=?",
                    (identifier,),
                ).fetchone()
            if fingerprint and row["credential_fingerprint"] != fingerprint:
                increment = 1 if row["credential_fingerprint"] else 0
                connection.execute(
                    "UPDATE managed_mailbox_identities SET credential_fingerprint=?, "
                    "revision=revision+? WHERE id=?",
                    (fingerprint, increment, row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM managed_mailbox_identities WHERE id=?",
                    (row["id"],),
                ).fetchone()
            if row["inbox_validity"] != validity:
                # Never guess whether an out-of-band replacement kept the same owner.
                raise ServiceError("mailbox_identity_reconciliation_required", 409)
            return {
                "id": row["id"],
                "email": email,
                "revision": row["revision"],
                "inbox_validity": validity,
            }

    def changing(
        self, email: str, retire: bool = False, expected: tuple[str, int] | None = None
    ) -> None:
        email = address(email)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM managed_mailbox_identities WHERE email=? AND state!='retired'",
                (email,),
            ).fetchone()
        if row is None:
            self.lookup(email)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM managed_mailbox_identities WHERE email=? AND state!='retired'",
                (email,),
            ).fetchone()
            if row is None or row["state"] != "active":
                raise ServiceError("mailbox_security_change_pending", 409)
            if expected is not None and (row["id"], row["revision"]) != expected:
                raise ServiceError("mailbox_security_revision_changed", 409)
            connection.execute(
                "UPDATE managed_mailbox_identities SET revision=revision+1,state=? WHERE id=?",
                ("retired" if retire else "changing", row["id"]),
            )

    def prepare_create(self, email: str) -> None:
        try:
            self.lookup(email)
        except ServiceError as error:
            if error.status != 404:
                raise
        else:
            raise ServiceError("account_conflict", 409)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE managed_mailbox_identities SET state='retired',revision=revision+1 "
                "WHERE email=? AND state!='retired'",
                (email,),
            )

    def deleted(self, email: str, complete: bool) -> None:
        if complete:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE managed_mailbox_identities SET state='retired',revision=revision+1 "
                    "WHERE email=? AND state='changing'",
                    (email,),
                )

    def changed(self, email: str, complete: bool) -> None:
        if complete:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE managed_mailbox_identities SET revision=revision+1,state='active' "
                    "WHERE email=? AND state='changing'",
                    (address(email),),
                )
