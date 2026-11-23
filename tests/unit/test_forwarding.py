import json
import os
import sqlite3
import unittest
import uuid

from helper.forwarding import ForwardingControl, validate_forwarding
from shared.errors import ServiceError
from tests.support import StateFixture


class ForwardingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        data = self.fixture.root / "mail"
        data.mkdir()
        with sqlite3.connect(data / "imapsql.db") as db:
            db.executescript("""CREATE TABLE users(id INTEGER,username TEXT);
              CREATE TABLE extKeys(id TEXT,uid INTEGER);CREATE TABLE mboxes(id INTEGER,name TEXT);
              CREATE TABLE msgs(extBodyKey TEXT,mboxId INTEGER,date REAL,compressAlgo TEXT);
              INSERT INTO users VALUES(1,'mail@rsalehin24.me');
              INSERT INTO mboxes VALUES(1,'INBOX');INSERT INTO extKeys VALUES('old',1);
              INSERT INTO msgs VALUES('old',1,1,'');""")
        self.control = ForwardingControl(self.fixture.root, data, worker_owner=os.getuid())

    def test_worker_records_are_expected_from_root_whoever_owns_the_state_directory(self):
        self.assertEqual(ForwardingControl(self.fixture.root, self.fixture.root).worker_owner, 0)

    def save(self, **changes):
        value = {
            "email": "mail@rsalehin24.me",
            "destination": "recipient@example.com",
            "enabled": True,
            "revision": self.control.listing()["revision"],
            **changes,
        }
        return self.control.handle("forwarding_save", str(uuid.uuid4()), value, self.fixture.now)

    def test_direct_save_baselines_existing_mail_and_preserves_local_messages(self):
        result = self.save()
        self.assertEqual(result["rules"][0]["destination"], "recipient@example.com")
        self.assertTrue(result["rules"][0]["keep_local"])
        with self.control.connect() as db:
            self.assertEqual(db.execute("SELECT state FROM messages").fetchone()[0], "baseline")
        result = self.save(enabled=False)
        self.assertFalse(result["rules"][0]["enabled"])
        self.control.handle(
            "forwarding_remove",
            str(uuid.uuid4()),
            {"email": "mail@rsalehin24.me", "revision": result["revision"]},
            self.fixture.now,
        )
        self.assertEqual(self.control.listing()["rules"], [])
        with sqlite3.connect(self.control.data / "imapsql.db") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM msgs").fetchone()[0], 1)

    def test_missing_mailbox_invalid_recipient_revision_and_unsafe_retry_are_rejected(self):
        with self.assertRaisesRegex(ServiceError, "account_not_found"):
            self.save(email="missing@rsalehin24.me")
        with self.assertRaisesRegex(ServiceError, "invalid_forwarding_destination"):
            self.save(destination="bad\r\nBcc:other@example.com")
        self.save()
        with self.assertRaisesRegex(ServiceError, "forwarding_settings_changed"):
            self.save(revision=0)
        with self.assertRaisesRegex(ServiceError, "forwarding_retry_acknowledgment_required"):
            validate_forwarding(
                {
                    "protocol": 1,
                    "request_id": str(uuid.uuid4()),
                    "operation": "forwarding_retry",
                    "email": "mail@rsalehin24.me",
                    "acknowledged": False,
                }
            )
        self.assertEqual(os.stat(self.control.path).st_mode & 0o777, 0o600)

    def test_replayed_request_cannot_change_the_requested_rule(self):
        identifier = str(uuid.uuid4())
        values = {
            "email": "mail@rsalehin24.me",
            "destination": "recipient@example.com",
            "enabled": True,
            "revision": 0,
        }
        first = self.control.handle("forwarding_save", identifier, values, self.fixture.now)
        second = self.control.handle("forwarding_save", identifier, values, self.fixture.now)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ServiceError, "request_id_conflict"):
            self.control.handle(
                "forwarding_save",
                identifier,
                {**values, "destination": "other@example.com"},
                self.fixture.now,
            )
        with self.control.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0], 1)
        self.assertNotIn("body", json.dumps(second))
