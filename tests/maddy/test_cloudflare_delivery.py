"""Real 0.9.6 rewrite of Cloudflare's verified local-delivery addresses."""

import smtplib
import unittest
import uuid
from email.message import EmailMessage

from helper.protocol import HelperRequest
from shared.cloudflare_delivery import delivery_configuration
from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy.test_security_upgrade import SecurityFixture
from tests.maddy_support import imap_login
from tests.restore_support import bodies

BASE = '''hostname mail.rsalehin24.me
state_dir /data
runtime_dir /run/maddy
$(primary_domain) = rsalehin24.me
$(local_domains) = $(primary_domain)
tls file /data/tls/fullchain.pem /data/tls/privkey.pem
auth.pass_table local_authdb {
 table sql_table {
  driver sqlite3
  dsn credentials.db
  table_name passwords
 }
}
storage.imapsql local_mailboxes {
 driver sqlite3
 dsn imapsql.db
}
table.chain local_rewrites {
 optional_step regexp "(.+)\\+(.+)@(.+)" "$1@$3"
}
msgpipeline local_routing {
 destination $(local_domains) {
  modify {
   replace_rcpt &local_rewrites
  }
  deliver_to &local_mailboxes
 }
 default_destination {
  reject 550 5.1.1 "Unknown mailbox"
 }
}
smtp tcp://0.0.0.0:25 {
 default_source {
  default_destination {
   deliver_to &local_routing
  }
 }
}
imap tls://0.0.0.0:993 {
 auth &local_authdb
 storage &local_mailboxes
}
'''


class CloudflareDeliveryTests(unittest.TestCase):
    def test_delivery_alias_keeps_original_headers_and_body_and_cannot_relay(self):
        fixture = SecurityFixture(delivery_configuration(BASE))
        self.addCleanup(fixture.close)
        fixture.start()
        email = 'website@rsalehin24.me'
        fixture.helper.handle(HelperRequest('create', str(uuid.uuid4()), email, MAILBOX_PASSWORD))
        message = EmailMessage()
        message['From'] = 'Original sender <original@example.net>'
        message['To'] = email
        message['Subject'] = 'Original subject remains intact'
        message['Message-ID'] = '<' + str(uuid.uuid4()) + '@example.net>'
        message.set_content('Original body remains intact.')
        with smtplib.SMTP('127.0.0.1', fixture.smtp_port, timeout=5) as smtp:
            self.assertEqual(smtp.sendmail('original@example.net',
                             ['website@delivery.rsalehin24.me'], message.as_bytes()), {})
            with self.assertRaises(smtplib.SMTPRecipientsRefused):
                smtp.sendmail('original@example.net', ['other@example.org'], message.as_bytes())
        with imap_login(fixture, email, MAILBOX_PASSWORD) as imap:
            received = bodies(imap)
            self.assertIn(b'Original sender <original@example.net>', received)
            self.assertIn(b'Original subject remains intact', received)
            self.assertIn(b'Original body remains intact.', received)
            self.assertIn(message['Message-ID'].encode(), received)
        self.assertEqual(delivery_configuration(fixture.configuration.read_text()),
                         fixture.configuration.read_text())
