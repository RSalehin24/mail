import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.host_tls_support import connect_tls
from tests.tls_support import CertificateFixture, TlsPeer


class HostTlsTransportTests(unittest.TestCase):
    def test_dns_only_mail_certificate_is_verified_over_loopback_without_dns_routing(self):
        with tempfile.TemporaryDirectory() as temporary:
            issuer = CertificateFixture(Path(temporary))
            peer = TlsPeer(issuer.issue(loopback=False))
            self.addCleanup(peer.close)
            context = ssl.create_default_context(cafile=str(issuer.trust))
            create = socket.create_connection
            with patch('tests.host_tls_support.socket.create_connection', wraps=create) as connect:
                with connect_tls(context, peer.server.server_address[1], 5):
                    pass
            connect.assert_called_once_with(('127.0.0.1', peer.server.server_address[1]), timeout=5)

    def test_foreign_hostname_and_untrusted_issuer_remain_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            issuer = CertificateFixture(root)
            other = root / 'other'
            other.mkdir()
            foreign_issuer = CertificateFixture(other)
            for pair in (issuer.issue('wrong-host', hostname='wrong.example.org', loopback=False),
                         foreign_issuer.issue(loopback=False)):
                peer = TlsPeer(pair)
                try:
                    context = ssl.create_default_context(cafile=str(issuer.trust))
                    with self.assertRaises(ssl.SSLCertVerificationError):
                        connect_tls(context, peer.server.server_address[1], 5)
                finally:
                    peer.close()
