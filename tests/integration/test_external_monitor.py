import os
import socket
import time
import unittest
from unittest.mock import MagicMock, patch

from host.monitor import Monitor
from shared.validation import MAIL_HOST
from tests.support import StateFixture
from tests.tls_support import CertificateFixture, TlsPeer


class ExternalMonitorTests(unittest.TestCase):
    def setUp(self):
        self.state = StateFixture()
        self.addCleanup(self.state.close)
        self.connect = socket.create_connection
        certificates = self.state.root / "certificates"
        certificates.mkdir()
        self.issuer = CertificateFixture(certificates)

    def peer(self, certificate):
        peer = TlsPeer(certificate)
        self.addCleanup(peer.close)
        return peer

    def probe(self, peer):
        alerts = MagicMock()

        def loopback(endpoint, *arguments, **keywords):
            self.assertIn(endpoint, ((MAIL_HOST, 993), (MAIL_HOST, 443)))
            return self.connect(peer.server.server_address, *arguments, **keywords)

        with (
            patch("host.certificates.socket.create_connection", side_effect=loopback),
            patch.dict(os.environ, {"SSL_CERT_FILE": str(self.issuer.trust)}),
        ):
            Monitor(alerts, ({}, time.time)).external()
        return alerts

    def test_real_trusted_tls_warns_about_the_expiring_served_pair_on_both_consumers(self):
        alerts = self.probe(self.peer(self.issuer.issue()))
        for port in (993, 443):
            alerts.observe.assert_any_call(f"external_tls_{port}", "critical_7_days")
        alerts.flush.assert_called_once()

    def test_real_wrong_hostname_is_untrusted_instead_of_a_successful_reachability_check(self):
        alerts = self.probe(self.peer(self.issuer.issue(hostname="wrong.example")))
        for port in (993, 443):
            alerts.observe.assert_any_call(f"external_tls_{port}", "unreachable_or_untrusted")
