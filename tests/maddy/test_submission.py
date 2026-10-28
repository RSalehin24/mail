"""Real 0.9.6 submission, verified relay TLS, durable queue and client-owned Sent."""

import shutil
import smtplib
import socket
import socketserver
import ssl
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from helper.protocol import HelperRequest
from shared.submission import SUBMISSION, relay_configuration
from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy.test_security_upgrade import SecurityFixture
from tests.maddy_support import CONFIGURATION, imap_login
from tests.smtp_support import SmtpHandler
from tests.tls_support import CertificateFixture


class RelayHandler(SmtpHandler):
    def hello(self, argument):
        if not self.server.fixture.offer_tls and not self.secured:
            self.reply(b"250 fixture without STARTTLS")
        else:
            super().hello(argument)

    def data(self, argument):
        self.reply(b"354 send message")
        raw = bytearray()
        while line := self.rfile.readline(65537):
            if line == b".\r\n":
                break
            raw.extend(line)
        fixture = self.server.fixture
        fixture.attempts += 1
        if fixture.result == 250:
            fixture.messages.append(bytes(raw))
        self.reply(str(fixture.result).encode() + b" fixture result")


class SubmissionFixture(SecurityFixture):
    additional_arguments = ("--network", "host")

    def refresh_ports(self, inspected=None):
        pass



class SubmissionTests(unittest.TestCase):
    def setUp(self):
        ports = []
        for _ in range(3):
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                ports.append(listener.getsockname()[1])
        configuration = CONFIGURATION + '\nimport /data/submission-relay.conf\n' + SUBMISSION
        for original, port in zip((25, 993, 587), ports, strict=True):
            configuration = configuration.replace('0.0.0.0:' + str(original),
                                                  '127.0.0.1:' + str(port))
        self.fixture = SubmissionFixture(configuration)
        self.fixture.smtp_port, self.fixture.imap_port, self.fixture.submission_port = ports
        self.addCleanup(self.fixture.close)
        fixture = self.fixture
        relay_tls = fixture.certificates.issue(name="relay", hostname="smtp-relay.brevo.com")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(relay_tls / 'fullchain.pem', relay_tls / 'privkey.pem')
        self.peer = SimpleNamespace(context=context, implicit_tls=False, offer_tls=True,
                                    reject_auth=False, reject_sender=False,
                                    events=[], messages=[], disconnects=0, result=250, attempts=0)
        self.server = socketserver.ThreadingTCPServer(  # noqa: S104 -- private Docker fixture relay.
            ('127.0.0.1', 0), RelayHandler)
        self.server.daemon_threads = True
        self.server.fixture = self.peer
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_peer)
        self.relay = relay_configuration({'username': 'synthetic', 'credential': 'fixture-secret'})
        self.relay = self.relay.replace('smtp-relay.brevo.com:587',
            '127.0.0.1:' + str(self.server.server_address[1]))
        self.relay = self.relay.replace('    starttls yes',
            '    tls_client {\n        root_ca /data/fixture-ca.pem\n    }\n    starttls yes')
        (fixture.data / 'fixture-ca.pem').write_bytes(fixture.certificates.trust.read_bytes())
        (fixture.data / 'submission-relay.conf').write_text(self.relay)
        (fixture.data / 'submission-from.awk').write_bytes(
            Path('host/submission-from.awk').read_bytes())
        fixture.start()
        for name in ('sender', 'other'):
            fixture.helper.handle(HelperRequest('create', str(uuid.uuid4()),
                                  name + '@rsalehin24.me', MAILBOX_PASSWORD))
        self.email = 'sender@rsalehin24.me'

    def close_peer(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def connect(self, login=True):
        connection = smtplib.SMTP('127.0.0.1', self.fixture.submission_port, timeout=5)
        self.addCleanup(connection.close)
        connection.ehlo()
        connection.starttls(context=ssl.create_default_context(
            cafile=self.fixture.certificates.trust))
        connection.ehlo()
        if login:
            connection.login(self.email, MAILBOX_PASSWORD)
        return connection

    def message(self, sender=None):
        return (f'From: {sender or self.email}\r\nTo: receiver@example.net\r\n'
                f'Message-ID: <{uuid.uuid4()}@rsalehin24.me>\r\n'
                'Subject: isolated submission fixture\r\n\r\nfixture sent body\r\n').encode()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.1)
        logs = self.fixture.command(('logs', self.fixture.identifier))
        self.fail('Fixture outcome deadline exceeded: ' + logs.stdout + logs.stderr)

    def queued(self):
        return [p for p in (self.fixture.data / 'submission-queue').rglob('*') if p.is_file()]

    def restart(self):
        self.fixture.command(('restart', '--time', '1', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()

    def test_tls_password_sender_and_unauthenticated_relay_guards(self):
        with smtplib.SMTP('127.0.0.1', self.fixture.submission_port, timeout=5) as connection:
            connection.ehlo()
            self.assertNotIn('auth', connection.esmtp_features)
            self.assertGreaterEqual(connection.docmd('AUTH', 'PLAIN AHgAeA==')[0], 500)
            self.assertGreaterEqual(connection.mail(self.email)[0], 400)
        connection = self.connect(login=False)
        with self.assertRaises(smtplib.SMTPAuthenticationError):
            connection.login(self.email, 'wrong-password')
        with self.assertRaises(smtplib.SMTPException):
            connection.sendmail(self.email, ['receiver@example.net'], self.message())
        login = self.connect(login=False)
        login.user, login.password = self.email, MAILBOX_PASSWORD
        self.assertEqual(login.auth('LOGIN', login.auth_login)[0], 235)
        connection = self.connect()
        with self.assertRaises(smtplib.SMTPException):
            connection.sendmail('other@rsalehin24.me', ['receiver@example.net'], self.message())
        connection.rset()
        with self.assertRaises(smtplib.SMTPException):
            connection.sendmail(self.email, ['receiver@example.net'],
                                self.message('other@rsalehin24.me'))
        connection.rset()
        with self.assertRaises(smtplib.SMTPException):
            connection.sendmail(self.email, ['receiver@example.net'],
                                self.message('other@rsalehin24.me').replace(
                                    b'To:', ('Sender: ' + self.email + '\r\nTo:').encode()))
        self.assertFalse(self.peer.messages)

    def test_client_saves_exactly_one_sent_copy_visible_in_second_session_and_restore(self):
        raw = self.message()
        self.connect().sendmail(self.email, ['receiver@example.net'], raw)
        self.wait_for(lambda: len(self.peer.messages) == 1)
        with imap_login(self.fixture, self.email, MAILBOX_PASSWORD) as first:
            first.select('Sent')
            self.assertEqual(first.search(None, 'ALL')[1], [b''])
            self.assertEqual(first.append('Sent', '(\\Seen)', None, raw)[0], 'OK')
        with imap_login(self.fixture, self.email, MAILBOX_PASSWORD) as second:
            second.select('Sent')
            self.assertEqual(second.search(None, 'ALL')[1], [b'1'])
            self.assertIn(b'fixture sent body', second.fetch('1', '(RFC822)')[1][0][1])
        self.fixture.command(('stop', self.fixture.identifier))
        saved = self.fixture.root / 'backup-data'
        shutil.copytree(self.fixture.data, saved)
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        with imap_login(self.fixture, self.email, MAILBOX_PASSWORD) as connection:
            connection.select('Sent')
            connection.store('1', '+FLAGS', '\\Deleted')
            connection.expunge()
        self.fixture.command(('stop', self.fixture.identifier))
        shutil.rmtree(self.fixture.data)
        shutil.copytree(saved, self.fixture.data)
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        with imap_login(self.fixture, self.email, MAILBOX_PASSWORD) as connection:
            connection.select('Sent')
            self.assertEqual(connection.search(None, 'ALL')[1], [b'1'])
            self.assertIn(b'fixture sent body', connection.fetch('1', '(RFC822)')[1][0][1])
        self.assertTrue(all(secure for event, secure in self.peer.events
                            if event == 'authentication'))

    def test_temporary_failure_persists_queue_over_restart(self):
        self.peer.result = 451
        self.connect().sendmail(self.email, ['receiver@example.net'], self.message())
        self.wait_for(lambda: self.peer.attempts > 0)
        self.wait_for(lambda: bool(self.queued()))
        self.fixture.command(('stop', '--time', '1', self.fixture.identifier))
        before = {p.relative_to(self.fixture.data): p.read_bytes() for p in self.queued()}
        self.assertTrue(before)
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        self.assertEqual(
            {p.relative_to(self.fixture.data): p.read_bytes() for p in self.queued()}, before)
        self.assertFalse(self.peer.messages)

    def test_permanent_rejection_generates_local_bounce(self):
        self.peer.result = 554
        self.connect().sendmail(self.email, ['receiver@example.net'], self.message())
        def bounce():
            with imap_login(self.fixture, self.email, MAILBOX_PASSWORD) as connection:
                connection.select('INBOX')
                return connection.search(None, 'ALL')[1] != [b'']
        self.wait_for(bounce)
        self.assertEqual(self.peer.attempts, 1)
        self.assertFalse(self.peer.messages)

    def test_certificate_failure_never_sends_credentials_or_body(self):
        (self.fixture.root / 'unrelated').mkdir()
        unrelated = CertificateFixture(self.fixture.root / 'unrelated')
        (self.fixture.data / 'fixture-ca.pem').write_bytes(unrelated.trust.read_bytes())
        self.restart()
        self.connect().sendmail(self.email, ['receiver@example.net'], self.message())
        self.wait_for(lambda: self.peer.disconnects > 0)
        self.assertFalse(self.peer.events)
        self.assertFalse(self.peer.messages)

    def test_missing_starttls_never_sends_credentials_or_body(self):
        self.peer.offer_tls = False
        self.connect().sendmail(self.email, ['receiver@example.net'], self.message())
        self.wait_for(lambda: bool(self.queued()))
        time.sleep(.5)
        self.assertFalse(self.peer.events)
        self.assertFalse(self.peer.messages)
