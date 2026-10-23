import unittest
from unittest.mock import patch

from backend.quota import credit_fields
from backend.relay import Relay, RelayDependencies
from shared.errors import ServiceError
from shared.validation import OTP_SENDER
from tests.api_support import FakeRelayTransport
from tests.support import StateFixture


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.transport = FakeRelayTransport()
        self.relay = Relay(
            RelayDependencies(
                self.fixture.database, self.fixture.keys, self.transport, lambda: self.fixture.now
            )
        )
        self.settings = {
            "host": "smtp-relay.brevo.com",
            "port": 587,
            "tls_mode": "starttls",
            "username": "fixture-login",
            "credential": "private fixture credential",
        }
        self.relay.save(self.settings)

    def test_connection_acceptance_and_receipt_are_distinct(self):
        connection = self.relay.test(OTP_SENDER, ("connection", "peer"))
        self.assertTrue(connection["connection_ok"])
        self.assertFalse(self.relay.otp_ready())
        with self.assertRaises(ServiceError):
            self.relay.confirm(connection["test_id"])
        delivery = self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.assertTrue(delivery["smtp_accepted"])
        self.assertFalse(self.relay.otp_ready())
        self.relay.confirm(delivery["test_id"])
        self.assertTrue(self.relay.otp_ready())
        self.assertIn(delivery["test_id"], self.transport.messages[-1][1].body)

    def test_failed_wrong_revision_and_unknown_tests_cannot_confirm(self):
        self.transport.failure = ServiceError("tls_failed", 502)
        failed = self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.assertFalse(failed["smtp_accepted"])
        for identifier in (failed["test_id"], "unknown"):
            with self.assertRaises(ServiceError):
                self.relay.confirm(identifier)
        self.transport.failure = None
        delivered = self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.relay.save(self.settings)
        with self.assertRaises(ServiceError):
            self.relay.confirm(delivered["test_id"])

    def test_mailbox_sender_test_does_not_enable_global_otp(self):
        delivery = self.relay.test("fixture@rsalehin24.me", ("delivery", "peer"))
        self.relay.confirm(delivery["test_id"])
        self.assertFalse(self.relay.otp_ready())

    def test_otp_send_uses_one_confirmed_revision_snapshot(self):
        delivery = self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.relay.confirm(delivery["test_id"])
        original = self.relay._configuration

        def racing_edit():
            saved = original()
            self.relay.save({**self.settings, "credential": "new untested fixture credential"})
            return saved

        with patch.object(self.relay, "_configuration", side_effect=racing_edit):
            self.relay.send_otp("123456")
        configuration, _ = self.transport.messages[-1]
        self.assertEqual(configuration.credential, self.settings["credential"])
        self.assertFalse(self.relay.otp_ready())

    def test_optional_provider_credits_do_not_expose_contact_or_api_key(self):
        self.assertEqual(self.relay.quota()["state"], "not_configured")
        fields = credit_fields(
            {
                "email": "private@example.net",
                "plan": [
                    {"credits": 280, "creditsType": "sendLimit", "type": "free"},
                    {"credits": True, "type": "invalid"},
                    {"credits": 10, "creditsType": "sendLimit", "type": "sms"},
                ],
            }
        )
        self.assertEqual(fields, [{"plan": "free", "credits": 280, "credits_type": "sendLimit"}])
        self.assertNotIn("private", str(fields))

    def test_masked_settings_do_not_expose_secrets_and_save_invalidates_readiness(self):
        delivery = self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.relay.confirm(delivery["test_id"])
        self.assertNotIn(self.settings["credential"], str(self.relay.masked()))
        self.assertEqual(self.relay.reveal(), self.settings["credential"])
        self.relay.save(self.settings)
        self.assertFalse(self.relay.otp_ready())
        with self.fixture.database.transaction() as connection:
            self.assertNotIn(
                self.settings["credential"],
                str(list(connection.execute("SELECT * FROM audit_logs"))),
            )

    def test_fixed_endpoint_and_shared_send_budgets(self):
        for change in ({"host": "127.0.0.1"}, {"port": 25}, {"tls_mode": "tls"}):
            with self.assertRaises(ServiceError):
                self.relay.save({**self.settings, **change})
        for _ in range(3):
            self.relay.test(OTP_SENDER, ("delivery", "peer"))
        with self.assertRaises(ServiceError) as caught:
            self.relay.test(OTP_SENDER, ("delivery", "peer"))
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(self.transport.messages), 3)

    def test_invalid_unicode_or_header_values_cannot_replace_the_saved_revision(self):
        revision = self.relay.masked()["revision"]
        for change in (
            {"credential": "\ud800"},
            {"credential": "é" * 2049},
            {"api_key": "\ud800"},
            {"api_key": "injected\r\nheader"},
            {"api_key": 42},
            {"username": ""},
        ):
            with self.assertRaises(ServiceError):
                self.relay.save({**self.settings, **change})
            self.assertEqual(self.relay.masked()["revision"], revision)
        self.assertEqual(self.relay.reveal(), self.settings["credential"])
