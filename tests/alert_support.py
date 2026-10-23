"""One real independent SMTP alert outbox for loopback delivery checks."""

import os
import socket
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from host.alerts import Alerts, SmtpAlertTransport
from host.locks import LockPolicy
from host.monitor import Monitor
from shared.files import atomic_json, protected_json
from shared.smtp import SMTP_HOST
from shared.validation import MAIL_HOST
from tests.smtp_support import SmtpFixture
from tests.support import StateFixture
from tests.tls_support import CertificateFixture, TlsPeer


def smtp_alerts(directory, clock):
    credentials, path = directory / 'smtp.json', directory / 'alerts.json'
    atomic_json(credentials, {'username': 'synthetic', 'credential': 'fixture-secret'})
    atomic_json(path, {})
    path.with_suffix('.lock').touch(mode=0o600)
    transport = SmtpAlertTransport({'recipient': 'rsalehin24@gmail.com', 'port': 587,
                                    'credential_reference': str(credentials)})
    return Alerts(path, (transport, clock), LockPolicy(1, os.getuid()))


class AlertDeliveryFixture(unittest.TestCase):
    def setUp(self):
        self.state = StateFixture()
        self.addCleanup(self.state.close)
        self.state.now = time.time()
        self.connect = socket.create_connection
        self.smtp = SmtpFixture(self.state.root / 'smtp')
        self.addCleanup(self.smtp.close)
        directory = self.state.root / 'certificates'
        directory.mkdir()
        self.issuer = CertificateFixture(directory, lifetime_days=30)
        self.alerts = smtp_alerts(self.state.root, lambda: self.state.now)
        self.monitor = Monitor(self.alerts, ({}, lambda: self.state.now))
        self.state.database.path.rename(self.state.root / 'unavailable-dashboard.db')
        self.target_address = None

    def peer(self, pair):
        peer = TlsPeer(pair)
        self.addCleanup(peer.close)
        self.target_address = peer.server.server_address
        return peer

    def route(self, endpoint, *arguments, **keywords):
        if endpoint == (SMTP_HOST, 587):
            return self.connect(self.smtp.server.server_address, *arguments, **keywords)
        if endpoint in ((MAIL_HOST, 993), (MAIL_HOST, 443)):
            return self.connect(self.target_address, *arguments, **keywords)
        raise RuntimeError('Network destination outside the isolated alert fixtures')

    def delivery_scope(self):
        trust = self.state.root / 'combined-trust.pem'
        trust.write_bytes(self.issuer.trust.read_bytes()
                          + self.smtp.certificates.trust.read_bytes())
        scope = ExitStack()
        scope.enter_context(patch('socket.create_connection', side_effect=self.route))
        scope.enter_context(patch.dict(os.environ, {'SSL_CERT_FILE': str(trust)}))
        scope.enter_context(patch('host.alerts.protected_json',
                                 side_effect=lambda path: protected_json(path, os.getuid())))
        return scope

    def assert_received(self, condition, count):
        messages = [message for message in self.smtp.messages if condition.encode() in message]
        self.assertEqual(len(messages), count)
        self.assertTrue(all(b'fixture-secret' not in message for message in messages))
        self.assertTrue(all(secured for event, secured in self.smtp.events
                            if event == 'authentication'))
        self.assertIn(('recipient', 'TO:<rsalehin24@gmail.com>'), self.smtp.events)
        self.assertFalse(self.state.database.path.exists())

