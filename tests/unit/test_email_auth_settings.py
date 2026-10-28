import unittest

from backend.auth import Verification
from backend.email_auth import EmailAuthentication
from shared.errors import ServiceError
from tests.auth_support import AuthFixture


class EmailAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthFixture()
        self.addCleanup(self.fixture.close)
        self.settings = EmailAuthentication(self.fixture.auth)
        self.session = self.fixture.login()

    def test_recent_auth_and_current_delivery_are_required(self):
        with self.assertRaisesRegex(ServiceError, "recent_authentication_required"):
            self.settings.save(self.session, True)
        self.fixture.reauth(self.session)
        with self.assertRaisesRegex(ServiceError, "email_delivery_confirmation_required"):
            self.settings.save(self.session, True)
        self.fixture.ready_relay()
        self.assertTrue(self.settings.save(self.session, True)["available"])
        self.assertTrue(EmailAuthentication(self.fixture.auth).status()["enabled"])
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE relay_settings SET revision=revision+1")
        self.assertFalse(self.settings.status()["available"])
        self.assertTrue(self.settings.status()["enabled"])

    def test_disabling_revokes_outstanding_codes_and_survives_reload(self):
        self.fixture.reauth(self.session)
        self.fixture.ready_relay()
        self.settings.save(self.session, True)
        challenge = self.fixture.challenge()
        sent = []
        self.fixture.auth.send_otp(Verification(challenge, "email", "", "test"), sent.append)
        self.assertEqual(len(sent), 1)
        self.settings.save(self.session, False)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM otp_codes").fetchone()[0], 0)
        self.assertFalse(EmailAuthentication(self.fixture.auth).status()["enabled"])
        with self.assertRaisesRegex(ServiceError, "email_otp_unavailable"):
            self.fixture.auth.verify(Verification(challenge, "email", sent[0], "test"), "login")
