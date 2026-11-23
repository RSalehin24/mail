import os
import re
import threading
import time
import unittest
import uuid
from datetime import UTC, datetime

from backend.database import Database
from helper.direct_deletion import DeletionRequest, DeletionStorage, DirectDeletion
from helper.protocol import HelperRequest
from helper.recovery_records import RecoveryRecords
from helper.retention import RecoveryPolicy
from host.mailbox_archive import MailboxArchives
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login

ACCOUNT, OTHER = "direct@rsalehin24.me", "other@rsalehin24.me"
POLICY = RecoveryPolicy.parse({
    "schema_version": 1, "revision": 1, "enabled": True, "permanent_deletion_enabled": True,
    "default_retention": "7_days", "protected": {},
})
METADATA = {"display_name": "Direct", "notes": "n", "created": 1000}


class ChangingArchives(MailboxArchives):
    """Delivers one message right after the first capture, as mail arriving mid-delete would."""

    def __init__(self, *arguments, deliver=None, delivered=None):
        super().__init__(*arguments)
        self.deliver, self.delivered = deliver, delivered if delivered is not None else []

    def create(self, *arguments):
        result = super().create(*arguments)
        if self.deliver and not self.delivered:
            self.delivered.append(True)  # shared by every instance the factory builds
            self.deliver()
        return result


class DirectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        for email in (ACCOUNT, OTHER):
            self.fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )
        dashboard = self.fixture.root / "dashboard"
        dashboard.mkdir(mode=0o700)
        self.database = Database.provision(dashboard / "admin_data.db", str(uuid.uuid4()))
        with self.database.transaction() as connection:
            for email in (ACCOUNT, OTHER):
                connection.execute(
                    "INSERT INTO mailboxes (email,display_name,notes,created,updated,status) "
                    "VALUES(?,?,?,?,?,?)", (email, "Name", "Notes", 1000, 1000, "ready"))
        self.state = self.fixture.root / "state"
        self.state.mkdir(mode=0o700)
        self.records = RecoveryRecords(self.state / "recovery.db")
        self.owner = os.geteuid()

    def deletion(self, archives=MailboxArchives):
        def build():
            return archives(self.state / "recovery-points", self.state / "recovery.key",
                            self.owner)
        storage = DeletionStorage(self.fixture.data, self.database.path)
        return DirectDeletion(storage, (self.fixture.maddy, self.fixture.helper.journal,
                                        self.records, build)), build

    def request(self, email=ACCOUNT, permanent=False):
        return DeletionRequest(str(uuid.uuid4()), email, "none" if permanent else "7_days",
                               permanent, METADATA)

    def append(self, connection, number):
        body = f"Subject: live {number}\r\n\r\nbody {number}\r\n".encode()
        return connection.append("INBOX", "", datetime.now(UTC), body)[0] == "OK"

    def test_delete_keeps_maddy_running_and_archives_every_acknowledged_message(self):
        acknowledged, stop = [], threading.Event()

        def writer():
            try:
                with imap_login(self.fixture, ACCOUNT, MAILBOX_PASSWORD) as connection:
                    for number in range(400):
                        if stop.is_set() or not self.append(connection, number):
                            return
                        acknowledged.append(number)
                        time.sleep(0.01)
            except Exception:  # noqa: BLE001, S110 -- the account disappears under this writer
                return

        thread = threading.Thread(target=writer)
        thread.start()
        while len(acknowledged) < 5:
            time.sleep(0.01)
        deletion, build = self.deletion()
        request = self.request()
        started = time.monotonic()
        result = deletion.delete(request, POLICY, int(time.time()))
        elapsed = time.monotonic() - started
        stop.set()
        thread.join()
        self.assertEqual(result["state"], "complete")
        inventory = self.fixture.maddy.inventories()
        self.assertEqual((inventory["storage"], inventory["credentials"]), ([OTHER], [OTHER]))
        record = self.records.get(request.identifier)
        self.assertEqual(record["state"], "deleted")
        archives = build()
        self.assertTrue(archives.available(record["snapshot"], ACCOUNT))
        with archives.open(record["snapshot"], ACCOUNT) as mailbox:
            import sqlite3
            with sqlite3.connect(mailbox / "imapsql.db") as connection:
                archived = connection.execute("SELECT COUNT(*) FROM msgs").fetchone()[0]
        self.assertEqual(archived, len(acknowledged))
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT email FROM mailboxes").fetchall()
        self.assertEqual([row[0] for row in rows], [OTHER])
        with imap_login(self.fixture, OTHER, MAILBOX_PASSWORD) as connection:
            self.assertEqual(connection.select("INBOX")[0], "OK")
        print(f"\ndirect delete {elapsed:.2f}s, {archived} messages archived, Maddy kept running")

    def test_mail_arriving_during_the_archive_is_archived_too(self):
        def late_message():
            with imap_login(self.fixture, ACCOUNT, MAILBOX_PASSWORD) as connection:
                self.assertTrue(self.append(connection, 999))

        once = []
        deletion, build = self.deletion(
            lambda *a: ChangingArchives(*a, deliver=late_message, delivered=once))
        request = self.request()
        deletion.delete(request, POLICY, int(time.time()))
        record = self.records.get(request.identifier)
        with build().open(record["snapshot"], ACCOUNT) as mailbox:
            import sqlite3
            with sqlite3.connect(mailbox / "imapsql.db") as connection:
                subjects = [row[0] for row in connection.execute("SELECT cachedHeader FROM msgs")]
        self.assertTrue(any(b"live 999" in (value or b"") for value in subjects))

    def test_a_repeated_request_is_idempotent(self):
        deletion, _ = self.deletion()
        request = self.request()
        deletion.delete(request, POLICY, int(time.time()))
        again = deletion.delete(request, POLICY, int(time.time()))
        self.assertEqual(again["state"], "complete")

    def test_permanent_deletion_makes_no_archive(self):
        deletion, _ = self.deletion()
        request = self.request(permanent=True)
        deletion.delete(request, POLICY, int(time.time()))
        record = self.records.get(request.identifier)
        self.assertEqual((record["state"], record["snapshot"]), ("removed", None))
        self.assertEqual(self.fixture.maddy.inventories()["storage"], [OTHER])

    def test_an_unknown_account_is_refused_and_leaves_no_stuck_record(self):
        deletion, _ = self.deletion()
        with self.assertRaisesRegex(ServiceError, re.escape("account_not_found")):
            deletion.delete(self.request("missing@rsalehin24.me"), POLICY, int(time.time()))
        self.assertEqual(self.fixture.maddy.inventories()["storage"], [ACCOUNT, OTHER])
        self.assertEqual(self.records.listing(), [])
        self.assertEqual(deletion.delete(self.request(), POLICY, int(time.time()))["state"],
                         "complete")


if __name__ == "__main__":
    unittest.main()
