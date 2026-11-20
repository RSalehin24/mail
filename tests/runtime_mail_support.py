"""Real Maddy plus the real dashboard image, enrolled and logged in over HTTPS."""

import unittest

from tests.maddy_support import UnprivilegedMaddyFixture
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_crash_support import wait_for_complete
from tests.runtime_support import RuntimeFixture


class MailFlowFixture(unittest.TestCase):
    def setUp(self):
        self.maddy = UnprivilegedMaddyFixture()
        self.addCleanup(self.maddy.close)
        self.maddy.start()
        self.web = RuntimeFixture(self.maddy)
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
        record = wait_for_complete(self.web.journal(), payload["request_id"])
        self.assertEqual(record["state"], "complete")
        # Only retry after trusted completion, using the original ID; no command may repeat.
        status, _, result = self.web.api("POST", path, payload)
        self.assertEqual(status, 200)
        self.assertEqual(result["state"], "complete")
