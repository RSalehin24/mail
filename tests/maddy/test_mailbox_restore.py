import os
import shutil
import sqlite3
import time
import unittest
import uuid

from backend.database import Database
from helper.direct_deletion import DeletionRequest, DeletionStorage, DirectDeletion
from helper.journal import Journal
from helper.protocol import HelperRequest
from helper.recovery_records import RecoveryRecords
from helper.retention import RecoveryPolicy
from host.expire_recovery import expire
from host.mailbox_archive import MailboxArchives
from host.restore_mailbox import MailboxRestore
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import deliver

ACCOUNT, OTHER = "restore@rsalehin24.me", "other@rsalehin24.me"
NEW_PASSWORD = "a brand new mailbox password 24"
POLICY = RecoveryPolicy.parse({
    "schema_version": 1, "revision": 1, "enabled": True, "permanent_deletion_enabled": False,
    "default_retention": "7_days", "protected": {},
})


class MailboxRestoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        for email in (ACCOUNT, OTHER):
            self.fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )
        deliver(self.fixture, ACCOUNT, "kept-through-delete-and-restore")
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
        self.owner = os.geteuid()
        shutil.copy(self.fixture.helper.journal.path, self.state / "journal.db")
        (self.state / "journal.db").chmod(0o600)
        self.journal = Journal(self.state / "journal.db", self.owner)
        self.records = RecoveryRecords(self.state / "recovery.db")
        self.deployment = {
            "maddy_data_directory": str(self.fixture.data),
            "backup_sources": {"dashboard": str(dashboard)}, "state_owner_uid": self.owner,
        }
        storage = DeletionStorage(self.fixture.data, self.database.path)
        self.deletion = DirectDeletion(storage, (
            self.fixture.maddy, self.journal, self.records,
            lambda: MailboxArchives(self.state / "recovery-points", self.state / "recovery.key",
                                    self.owner)))
        self.request = DeletionRequest(str(uuid.uuid4()), ACCOUNT, "7_days", False,
                                       {"display_name": "Name", "notes": "Notes", "created": 1000})
        self.deletion.delete(self.request, POLICY, int(time.time()))

    def restorer(self):
        return MailboxRestore(self.deployment, self.state, self.fixture.maddy)

    def test_restore_returns_the_mail_with_the_new_password_while_maddy_runs(self):
        self.assertNotIn(ACCOUNT, self.fixture.maddy.inventories()["storage"])
        started = time.monotonic()
        email = self.restorer().restore(self.request.identifier, NEW_PASSWORD)
        elapsed = time.monotonic() - started
        self.assertEqual(email, ACCOUNT)
        with imap_login(self.fixture, ACCOUNT, NEW_PASSWORD) as connection:
            connection.select("INBOX")
            identifiers = connection.search(None, "ALL")[1][0].split()
            messages = [connection.fetch(item, "(RFC822)")[1][0][1] for item in identifiers]
            self.assertTrue(any(b"kept-through-delete-and-restore" in body for body in messages))
        with imap_login(self.fixture, OTHER, MAILBOX_PASSWORD) as connection:
            self.assertEqual(connection.select("INBOX")[0], "OK")
        self.assertEqual(self.records.get(self.request.identifier)["state"], "restored")
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT status FROM mailboxes WHERE email=?", (ACCOUNT,)).fetchone()[0], "ready")
        print(f"\nrestore {elapsed:.2f}s, Maddy kept running")

    def test_a_mailbox_that_exists_again_cannot_be_restored_over(self):
        self.restorer().restore(self.request.identifier, NEW_PASSWORD)
        with self.assertRaises(ServiceError):
            self.restorer().restore(self.request.identifier, NEW_PASSWORD)

    def test_expired_recovery_is_removed_by_the_scheduled_job(self):
        with sqlite3.connect(self.records.path) as connection:
            connection.execute("UPDATE recovery_records SET deadline=1")
        self.assertEqual(expire(self.state, self.owner), 1)
        self.assertEqual(self.records.get(self.request.identifier)["state"], "removed")
        archive = self.state / "recovery-points" / (self.records.get(
            self.request.identifier)["snapshot"] + ".enc")
        self.assertFalse(archive.exists())


if __name__ == "__main__":
    unittest.main()
