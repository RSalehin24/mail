import os
import ssl
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.relay import SavedRelay, SmtpTransport
from host.alerts import SmtpAlertTransport
from host.cli import main
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.smtp import OutboundMessage
from tests.support import StateFixture


class SmtpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.credentials = self.fixture.root / "alert-credential.json"
        atomic_json(self.credentials, {"username": "synthetic", "credential": "fixture-secret"})
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        self.client.send_message.return_value = {}
        self.configuration = {
            "provider": "brevo_smtp",
            "recipient": "rsalehin24@gmail.com",
            "port": 587,
            "credential_reference": str(self.credentials),
        }

    def test_starttls_precedes_authentication_and_uses_only_the_fixed_recipient(self):
        with patch("shared.smtp.smtplib.SMTP", return_value=self.client) as constructor:
            relay = SavedRelay("smtp-relay.brevo.com", 587, "synthetic", "fixture-secret", 1)
            SmtpTransport(5).perform(relay, OutboundMessage("mail@rsalehin24.me", "Test", "Body"))
        constructor.assert_called_once_with("smtp-relay.brevo.com", 587, timeout=5)
        context = self.client.starttls.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        methods = [call[0] for call in self.client.method_calls]
        self.assertLess(methods.index("starttls"), methods.index("login"))
        self.assertLess(methods.index("login"), methods.index("send_message"))
        self.assertEqual(
            self.client.send_message.call_args.kwargs["to_addrs"], ["rsalehin24@gmail.com"]
        )

    def test_tls_failure_cannot_authenticate_or_send_and_remains_a_safe_error(self):
        self.client.starttls.side_effect = ssl.SSLCertVerificationError("fixture invalid TLS")
        with patch("shared.smtp.smtplib.SMTP", return_value=self.client):
            relay = SavedRelay("smtp-relay.brevo.com", 587, "synthetic", "fixture-secret", 1)
            with self.assertRaises(ServiceError) as caught:
                SmtpTransport(5).perform(relay, None)
        self.assertEqual(caught.exception.code, "relay_tls_auth_or_send_failed")
        self.client.login.assert_not_called()
        self.client.send_message.assert_not_called()

    def test_independent_email_alert_uses_root_credentials_without_dashboard_state(self):
        with (
            patch("shared.smtp.smtplib.SMTP_SSL", return_value=self.client) as constructor,
            patch(
                "host.alerts.protected_json",
                side_effect=lambda path: protected_json(path, os.getuid()),
            ),
        ):
            transport = SmtpAlertTransport({**self.configuration, "port": 465})
            transport.deliver({"id": "fixture-event", "name": "backup", "condition": "failed"})
        constructor.assert_called_once()
        self.client.starttls.assert_not_called()
        envelope = self.client.send_message.call_args.args[0]
        self.assertEqual(envelope["To"], "rsalehin24@gmail.com")
        self.assertIn("backup", envelope["Subject"])
        self.assertNotIn("fixture-secret", str(envelope))

    def test_another_alert_recipient_is_refused_before_network_access(self):
        with patch("shared.smtp.smtplib.SMTP") as constructor:
            with self.assertRaises(ServiceError):
                SmtpAlertTransport(
                    {**self.configuration, "recipient": "other@example.net"}
                ).deliver({})
        constructor.assert_not_called()

    def test_external_monitor_entry_requires_no_mail_host_database_journal_or_generations(self):
        with (
            patch("sys.argv", ["host.cli", "monitor-external"]),
            patch("host.cli.os.geteuid", return_value=0),
            patch(
                "host.cli.protected_json", return_value={"alert_configuration": self.configuration}
            ) as read,
            patch("host.cli.alert_transport", return_value=MagicMock()),
            patch("host.cli.Alerts"),
            patch("host.cli.Monitor") as monitor,
            patch("host.cli.HostContext.load") as host,
        ):
            self.assertEqual(main(), 0)
        host.assert_not_called()
        read.assert_called_once_with(Path("/etc/mail-admin/external-monitor.json"))
        monitor.return_value.external.assert_called_once_with()
