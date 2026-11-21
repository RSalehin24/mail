import unittest
from email import message_from_string
from email.message import EmailMessage
from unittest.mock import MagicMock

from shared.mail_templates import ALERT_HINTS, FALLBACK_HINT, operational_alert, verification_code
from shared.smtp import OutboundMessage, send_message


class MailTemplateTests(unittest.TestCase):
    def event(self, **changes):
        return {"id": "event-1", "name": "backup", "condition": "failed_or_missed",
                "observed_at": 1_790_000_000, **changes}

    def test_alert_has_readable_facts_and_no_json(self):
        subject, text, html = operational_alert(self.event())
        self.assertEqual(subject, "[Mail Admin] Action required: Backup - Failed or missed")
        for part in (text, html):
            self.assertNotIn("{", part)
            self.assertNotIn('"name"', part)
        self.assertIn("Check: Backup\nStatus: Failed or missed\nObserved: 2026-", text)
        self.assertIn("Reference: event-1", text)
        self.assertIn(ALERT_HINTS["backup"], text)

    def test_every_known_check_has_its_own_hint_and_unknown_uses_the_fallback(self):
        for name in ALERT_HINTS:
            self.assertIn(ALERT_HINTS[name], operational_alert(self.event(name=name))[1])
        self.assertIn(FALLBACK_HINT, operational_alert(self.event(name="other"))[1])

    def test_recovery_says_no_action_is_needed(self):
        subject, text, _ = operational_alert(self.event(condition="recovered"))
        self.assertIn("Recovered: Backup - Recovered", subject)
        self.assertIn("No action is needed", text)

    def test_missing_time_and_reference_are_labelled_not_provided(self):
        text = operational_alert({"name": "backup", "condition": "low"})[1]
        self.assertIn("Observed: Not provided", text)
        self.assertIn("Reference: Not provided", text)

    def test_html_escapes_event_values(self):
        html = operational_alert(self.event(id="<script>alert(1)</script>"))[2]
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_sent_message_is_multipart_with_plain_and_html_parts(self):
        connection = MagicMock()
        connection.send_message.return_value = {}
        message = OutboundMessage("mail@rsalehin24.me", *verification_code("123456"))
        send_message(connection, message)
        envelope: EmailMessage = connection.send_message.call_args.args[0]
        parsed = message_from_string(envelope.as_string())
        self.assertEqual(
            [part.get_content_type() for part in parsed.get_payload()], ["text/plain", "text/html"]
        )

    def test_plain_message_without_html_stays_single_part(self):
        connection = MagicMock()
        connection.send_message.return_value = {}
        send_message(connection, OutboundMessage("mail@rsalehin24.me", "Subject", "Body"))
        self.assertFalse(connection.send_message.call_args.args[0].is_multipart())
