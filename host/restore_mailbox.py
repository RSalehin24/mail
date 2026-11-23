"""Owner command: restore a deleted mailbox from its recovery archive, run as root on the VM.

    sudo python3 -m host.restore_mailbox <record-id>

Maddy keeps running. The command asks for the mailbox's new password.
"""

import argparse
import getpass
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from helper.journal import Journal
from helper.maddy import DockerMaddy, MaddyAccess, cli_succeeded
from helper.recovery_records import RecoveryRecords
from helper.state_database import STATE_FILE, shared_state_path
from host.mailbox_archive import MailboxArchives
from host.mailbox_store import body_mapping, connect, import_rows, stage_bodies
from host.mailbox_transaction import attached_transaction
from host.sync_forwarding import sync_forwarding
from shared.errors import ServiceError
from shared.files import protected_json, state_owner_uid
from shared.validation import mailbox_password, request_id

STATE = Path("/var/lib/mail-admin-helper")
DEPLOYMENT = Path("/etc/mail-admin/deployment.json")
LIVE_WRITE_WAIT_SECONDS = 5.0
RESTORABLE = {"deleted", "cancelled", "restoring"}


class MailboxRestore:
    def __init__(self, deployment: dict[str, Any], state: Path, maddy: MaddyAccess) -> None:
        owner = self.owner = state_owner_uid(deployment)
        self.data = Path(deployment["maddy_data_directory"])
        self.dashboard = Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db"
        self.records = RecoveryRecords(state / STATE_FILE)
        self.journal = Journal(state / STATE_FILE, owner)
        self.archives = MailboxArchives(state / "recovery-points", state / "recovery.key", owner)
        self.maddy = maddy

    def restore(self, record_id: str, password: str) -> str:
        record = self.records.get(request_id(record_id))
        self._admit(record)
        email = record["email"]
        with self.archives.open(record["snapshot"], email) as mailbox:
            keys = body_mapping(mailbox, record["id"])
            if record["state"] != "restoring":
                self._begin(record, mailbox, keys)
            inventory = self.maddy.inventories()
            if email not in inventory["storage"]:
                self._import(email, mailbox, keys)
            if email not in inventory["credentials"]:
                self._create_login(email, password)
            self._record_outcome(record, mailbox)
        return str(email)

    def _admit(self, record: dict[str, Any]) -> None:
        if record["state"] not in RESTORABLE:
            raise ServiceError("recovery_state_conflict", 409)
        expired = record["deadline"] is not None and time.time() >= record["deadline"]
        if expired and record["state"] != "restoring":
            raise ServiceError("recovery_expired", 409)

    def _begin(self, record: dict[str, Any], mailbox: Path, keys: dict[str, str]) -> None:
        self.archives.admit(mailbox / "imapsql.db", mailbox / "bodies", record["email"])
        self.maddy.validate()
        inventory = self.maddy.inventories()
        if any(record["email"] in inventory[key] for key in ("credentials", "storage")):
            raise ServiceError("account_conflict", 409)
        with connect(self.data / "imapsql.db") as live:
            taken = any(
                (self.data / "messages" / key).exists()
                or live.execute("SELECT 1 FROM extKeys WHERE id=?", (key,)).fetchone()
                for key in keys.values()
            )
        if taken:
            raise ServiceError("mailbox_import_body_conflict", 409)
        self.records.transition(record["id"], record["state"], "restoring", int(time.time()))

    def _import(self, email: str, mailbox: Path, keys: dict[str, str]) -> None:
        with connect(self.data / "imapsql.db", LIVE_WRITE_WAIT_SECONDS) as live:
            stage_bodies(live, self.data / "messages", mailbox, keys, resume=True)
            live.execute("BEGIN IMMEDIATE")
            import_rows(live, email, mailbox, keys)

    def _create_login(self, email: str, password: str) -> None:
        # Maddy hashes the password itself, as it does when an account is created.
        if not cli_succeeded(self.maddy.change(("creds", "create", email), password)):
            raise ServiceError("credentials_create_failed", 503)

    def _record_outcome(self, record: dict[str, Any], mailbox: Path) -> None:
        email, now = record["email"], int(time.time())
        metadata = protected_json(mailbox / "metadata.json", self.owner)
        paths = {
            "main": self.data / "credentials.db",
            "dashboard": self.dashboard,
            "state": shared_state_path(self.journal.path, self.records.path),
        }
        with attached_transaction(paths) as connection:
            self._restore_rows(connection, email, metadata)
            connection.execute(
                "UPDATE state.recovery_records SET state='restored',updated_at=? WHERE id=?",
                (now, record["id"]),
            )
            connection.execute(
                "INSERT INTO state.recovery_audit(created,action,request_id,email,outcome) "
                "VALUES(?,?,?,?,?)",
                (now, "recovery_restore", record["id"], email, "complete"),
            )

    @staticmethod
    def _restore_rows(
        connection: sqlite3.Connection, email: str, metadata: dict[str, Any]
    ) -> None:
        connection.execute(
            "INSERT INTO dashboard.mailboxes(email,display_name,notes,created,updated,status) "
            "VALUES(?,?,?,?,?,'ready') ON CONFLICT(email) DO UPDATE SET "
            "display_name=excluded.display_name,notes=excluded.notes,created=excluded.created,"
            "updated=excluded.updated,status='ready'",
            (email, metadata["display_name"], metadata["notes"], metadata["created"], time.time()),
        )
        connection.execute(
            "INSERT INTO state.incarnations VALUES(?,?,?,1) "
            "ON CONFLICT(email) DO UPDATE SET generation=excluded.generation,"
            "created=excluded.created,present=1",
            (email, str(uuid.uuid4()), time.time()),
        )


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("record_id", help="the deleted mailbox's record id, from the Deleted page")
    options = parser.parse_args(arguments)
    password = getpass.getpass("New mailbox password: ")
    if password != getpass.getpass("Repeat it: "):
        print("The passwords differ.", file=sys.stderr)
        return 2
    deployment = protected_json(DEPLOYMENT)
    restore = MailboxRestore(deployment, STATE, DockerMaddy(deployment))
    print("Restored", restore.restore(options.record_id, mailbox_password(password)))
    sync_forwarding()  # The mailbox needs its Cloudflare delivery route again.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
