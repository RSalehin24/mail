import json
import os
import unittest
from unittest.mock import patch

from backend.relay import Relay, RelayDependencies, SmtpTransport
from shared.errors import ServiceError
from shared.files import protected_json
from tests.alert_support import smtp_alerts
from tests.smtp_support import SmtpFixture
from tests.support import StateFixture
from tests.tls_support import CertificateFixture


class SmtpDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.state = StateFixture()
        self.addCleanup(self.state.close)

    def peer(self, certificate=None, implicit_tls=False):
        peer = SmtpFixture(self.state.root / "smtp", certificate, implicit_tls)
        self.addCleanup(peer.close)
        return peer

    def relay(self, port=587, credential="fixture-secret"):
        relay = Relay(
            RelayDependencies(
                self.state.database, self.state.keys, SmtpTransport(2), lambda: self.state.now
            )
        )
        relay.save(
            {
                "host": "smtp-relay.brevo.com",
                "port": port,
                "tls_mode": "tls" if port == 465 else "starttls",
                "username": "synthetic",
                "credential": credential,
            }
        )
        return relay

    def test_real_starttls_delivery_needs_the_exact_received_identifier_and_saved_revision(self):
        peer, relay = self.peer(), self.relay()
        connection = relay.test("mail@rsalehin24.me", ("connection", "fixture-peer"))
        self.assertTrue(connection["connection_ok"])
        self.assertEqual(peer.messages, [])
        self.assertFalse(relay.otp_ready())
        with self.assertRaises(ServiceError):
            relay.confirm(connection["test_id"])
        delivery = relay.test("mail@rsalehin24.me", ("delivery", "fixture-peer"))
        self.assertTrue(delivery["smtp_accepted"])
        self.assertFalse(relay.otp_ready())
        self.assertIn(delivery["test_id"].encode(), peer.messages[0])
        self.assertNotIn(b"fixture-secret", peer.messages[0])
        self.assertIn(("sender", "FROM:<mail@rsalehin24.me>"), peer.events)
        self.assertIn(("recipient", "TO:<rsalehin24@gmail.com>"), peer.events)
        self.assertTrue(all(secured for name, secured in peer.events if name == "authentication"))
        relay.confirm(delivery["test_id"])
        self.assertTrue(relay.otp_ready())
        self.relay()
        self.assertFalse(relay.otp_ready())
        with self.assertRaises(ServiceError):
            relay.confirm(delivery["test_id"])

    def test_real_implicit_tls_auth_sender_and_data_failures_do_not_enable_otp(self):
        peer, relay = self.peer(implicit_tls=True), self.relay(465)
        for name in ("reject_auth", "reject_sender", "reject_data"):
            with self.subTest(failure=name):
                setattr(peer, name, True)
                result = relay.test("mail@rsalehin24.me", ("delivery", "fixture-" + name))
                self.assertFalse(result["smtp_accepted"])
                self.assertFalse(relay.otp_ready())
                with self.assertRaises(ServiceError):
                    relay.confirm(result["test_id"])
                setattr(peer, name, False)
        self.assertEqual(peer.messages, [])

    def assert_tls_refused(self, certificate, trust):
        peer, relay = self.peer(certificate=certificate), self.relay()
        with patch.dict(os.environ, {"SSL_CERT_FILE": str(trust)}):
            result = relay.test("mail@rsalehin24.me", ("delivery", "fixture-peer"))
        self.assertFalse(result["smtp_accepted"])
        self.assertEqual(peer.events, [])
        self.assertEqual(peer.messages, [])

    def issuer(self, directory):
        path = self.state.root / directory
        path.mkdir()
        return CertificateFixture(path)

    def test_real_hostname_validation_failure_sends_neither_authentication_nor_message(self):
        certificates = self.issuer("other")
        wrong = certificates.issue(hostname="wrong.example")
        self.assert_tls_refused(wrong, certificates.trust)

    def test_real_expired_certificate_sends_neither_authentication_nor_message(self):
        certificates = self.issuer("expired")
        expired = certificates.issue(hostname="smtp-relay.brevo.com", expired=True)
        self.assert_tls_refused(expired, certificates.trust)

    def test_real_untrusted_certificate_sends_neither_authentication_nor_message(self):
        certificates = self.issuer("other")
        untrusted = certificates.issue(hostname="smtp-relay.brevo.com")
        trusted = self.issuer("trusted")
        self.assert_tls_refused(untrusted, trusted.trust)

    def test_unavailable_smtp_peer_cannot_confirm_a_receipt_or_enable_otp(self):
        peer, relay = self.peer(), self.relay()
        peer.server.shutdown()
        peer.server.server_close()
        result = relay.test("mail@rsalehin24.me", ("delivery", "fixture-peer"))
        self.assertFalse(result["smtp_accepted"])
        self.assertFalse(relay.otp_ready())
        with self.assertRaises(ServiceError):
            relay.confirm(result["test_id"])

    def test_non_ascii_smtp_credential_is_a_safe_failed_test_without_cleartext_authentication(self):
        peer, relay = self.peer(), self.relay(credential="synthetic-é")
        result = relay.test("mail@rsalehin24.me", ("delivery", "fixture-peer"))
        self.assertFalse(result["smtp_accepted"])
        self.assertFalse(relay.otp_ready())
        self.assertEqual(peer.events, [])
        self.assertEqual(peer.messages, [])

    def test_independent_alert_smtp_failure_is_persisted_and_retried_without_dashboard_state(self):
        peer = self.peer()
        peer.reject_auth = True
        alerts = smtp_alerts(self.state.root, lambda: self.state.now)
        path = alerts.path
        self.state.database.path.rename(self.state.root / "dashboard-stopped.db")
        with patch(
            "host.alerts.protected_json",
            side_effect=lambda source: protected_json(source, os.getuid()),
        ):
            alerts.observe("backup", "failed")
            with self.assertRaisesRegex(ServiceError, "notifications_pending"):
                alerts.flush()
            failed = json.loads(path.read_bytes())["backup"]
            self.assertEqual(failed["attempts"], 1)
            self.assertFalse(failed["delivered"])
            self.assertGreater(failed["next_attempt"], self.state.now)
            peer.reject_auth = False
            self.state.now = failed["next_attempt"]
            alerts.flush()
            alerts.observe("backup", "failed")
            alerts.flush()
        self.assertEqual(len(peer.messages), 1)
        self.assertIn(b"backup", peer.messages[0])
        self.assertTrue(json.loads(path.read_bytes())["backup"]["delivered"])
