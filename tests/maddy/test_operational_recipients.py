import smtplib
import unittest
import uuid

from helper.protocol import HelperRequest
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import CONFIGURATION, RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver

RECIPIENTS = ('postmaster', 'postmaster@rsalehin24.me', 'support@rsalehin24.me')
TARGET = 'operational-fixture@rsalehin24.me'
ROUTING = '''destination postmaster rsalehin24.me {
   modify {
    replace_rcpt static {
     entry postmaster operational-fixture@rsalehin24.me
     entry postmaster@rsalehin24.me operational-fixture@rsalehin24.me
     entry support@rsalehin24.me operational-fixture@rsalehin24.me
    }
   }
   deliver_to &local_mailboxes'''


class OperationalRecipientTests(unittest.TestCase):
    def setUp(self):
        original = 'destination rsalehin24.me {\n   deliver_to &local_mailboxes'
        self.assertEqual(CONFIGURATION.count(original), 1)
        self.fixture = RealMaddyFixture(CONFIGURATION.replace(original, ROUTING))
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        outcome = self.fixture.helper.handle(HelperRequest(
            'create', str(uuid.uuid4()), TARGET, MAILBOX_PASSWORD,
        ))
        self.assertEqual(outcome['state'], 'complete')

    def test_actual_smtp_postmaster_and_aliases_deliver_into_the_selected_target(self):
        for recipient in RECIPIENTS:
            with self.subTest(recipient=recipient):
                body = 'verified-operational-recipient-' + recipient
                deliver(self.fixture, recipient, body)
                with imap_login(self.fixture, TARGET, MAILBOX_PASSWORD) as connection:
                    self.assertIn(body.encode(), bodies(connection))

    def test_complete_case_insensitive_bare_tokens_deliver_but_lookalikes_and_senders_fail(self):
        for token in ('postmaster', 'POSTMASTER', '<postmaster>', '<PoStMaStEr>'):
            body = 'raw-operational-token-' + token
            with smtplib.SMTP('127.0.0.1', self.fixture.smtp_port, timeout=5) as smtp:
                smtp.ehlo('fixture.example')
                self.assertEqual(smtp.mail('sender@fixture.example')[0], 250)
                self.assertEqual(smtp.docmd('RCPT', 'TO:' + token)[0], 250)
                self.assertEqual(smtp.data('Subject: fixture\r\n\r\n' + body)[0], 250)
            with imap_login(self.fixture, TARGET, MAILBOX_PASSWORD) as connection:
                self.assertIn(body.encode(), bodies(connection))
        for token in ('xpostmaster', 'postmasterx', '<postmaster>suffix', '<postmaster',
                      'postmaster>', '<postmaster@>', '<postmaster@@rsalehin24.me>'):
            with (self.subTest(recipient=token),
                  smtplib.SMTP('127.0.0.1', self.fixture.smtp_port, timeout=5) as smtp):
                smtp.ehlo('fixture.example')
                self.assertEqual(smtp.mail('sender@fixture.example')[0], 250)
                self.assertIn(smtp.docmd('RCPT', 'TO:' + token)[0], {501, 550, 553})
        for token in ('postmaster', '<postmaster>'):
            with smtplib.SMTP('127.0.0.1', self.fixture.smtp_port, timeout=5) as smtp:
                smtp.ehlo('fixture.example')
                self.assertEqual(smtp.docmd('MAIL', 'FROM:' + token)[0], 501)

    def test_ordinary_recipient_delivery_and_esmtp_parameter_validation_are_preserved(self):
        email = 'ordinary-fixture@rsalehin24.me'
        result = self.fixture.helper.handle(HelperRequest(
            'create', str(uuid.uuid4()), email, MAILBOX_PASSWORD,
        ))
        self.assertEqual(result['state'], 'complete')
        deliver(self.fixture, email, 'ordinary-recipient-still-delivered')
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            self.assertIn(b'ordinary-recipient-still-delivered', bodies(connection))
        for recipient in ('<postmaster>', '<' + email + '>'):
            with smtplib.SMTP('127.0.0.1', self.fixture.smtp_port, timeout=5) as smtp:
                smtp.ehlo('fixture.example')
                self.assertEqual(smtp.mail('sender@fixture.example')[0], 250)
                self.assertEqual(smtp.docmd('RCPT', 'TO:' + recipient + ' UNKNOWN=fixture')[0], 500)
