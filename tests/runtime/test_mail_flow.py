import imaplib
import time
import unittest
import uuid
from unittest.mock import patch

from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class MailFlowFixture(unittest.TestCase):
    def setUp(self):
        self.maddy = RealMaddyFixture()
        self.addCleanup(self.maddy.close)
        self.maddy.start()
        self.web = RuntimeFixture(self.maddy.helper)
        self.addCleanup(self.web.close)
        self.web.start()
        self.authentication = RuntimeAuthentication(self, self.web)
        self.authentication.enroll()
        self.authentication.login()
        self.authentication.reauthenticate()

    def mutation(self, path, payload):
        status, _, result = self.web.api("POST", path, payload)
        self.assertIn(status, {200, 503})
        if status == 200:
            self.assertEqual(result["state"], "complete")
            return
        self.assertEqual(result["operation_id"], payload["request_id"])
        deadline = time.monotonic() + 30
        record = None
        while time.monotonic() < deadline:
            record = self.maddy.helper.journal.lookup(payload["request_id"])
            if record and record["state"] != "pending":
                break
            time.sleep(0.25)
        self.assertIsNotNone(record)
        self.assertEqual(record["state"], "complete")
        # Only retry after trusted completion, using the original ID; no command may repeat.
        status, _, result = self.web.api("POST", path, payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["state"], "complete")

class MailFlowTests(MailFlowFixture):
    def test_https_to_unix_helper_to_real_maddy_preserves_password_and_mail(self):
        email = "end-to-end@rsalehin24.me"
        original, replacement = (
            "  exact incoming fixture password  ",
            "  changed incoming fixture password  ",
        )
        identifier = str(uuid.uuid4())
        payload = {
            "request_id": identifier,
            "email": email,
            "password": original,
            "display_name": "End to end fixture",
            "notes": "Synthetic mail only",
        }
        with patch.object(self.maddy.maddy, "change", wraps=self.maddy.maddy.change) as commands:
            self.mutation("/api/accounts/create", payload)
            self.mutation("/api/accounts/create", payload)
            creates = [
                call for call in commands.call_args_list if call.args[0][:2] == ("creds", "create")
            ]
            self.assertEqual(len(creates), 1)
        deliver(self.maddy, email, "end-to-end-body-preserved")
        with imap_login(self.maddy, email, original) as established:
            self.assertIn(b"end-to-end-body-preserved", bodies(established))
            self.mutation(
                "/api/accounts/" + email + "/password",
                {"request_id": str(uuid.uuid4()), "password": replacement},
            )
            self.assertEqual(established.noop()[0], "OK")
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(self.maddy, email, original)
        with imap_login(self.maddy, email, replacement) as fresh:
            self.assertIn(b"end-to-end-body-preserved", bodies(fresh))
        status, _, accounts = self.web.api("GET", "/api/accounts")
        self.assertEqual(status, 200)
        self.assertEqual(accounts[0]["display_name"], payload["display_name"])
        database = self.web.database.path.read_bytes()
        for password in (original, replacement):
            self.assertNotIn(password.encode(), database)
