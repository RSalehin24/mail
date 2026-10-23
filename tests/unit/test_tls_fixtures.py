"""The private fixture CA must meet the strict TLS client's certificate policy."""

import subprocess
import unittest

from cryptography import x509

from tests.support import StateFixture
from tests.tls_support import CertificateFixture, openssl_executable


class FixtureCertificateTests(unittest.TestCase):
    def test_private_ca_has_critical_certificate_signing_usage_and_passes_strict_validation(self):
        state = StateFixture()
        self.addCleanup(state.close)
        certificates = CertificateFixture(state.root)
        pair = certificates.issue()
        usage = certificates.ca.extensions.get_extension_for_class(x509.KeyUsage)
        self.assertTrue(usage.critical)
        self.assertTrue(usage.value.key_cert_sign)
        command = openssl_executable()
        result = subprocess.run((command, 'verify', '-x509_strict', '-CAfile',
                                 str(certificates.trust), str(pair / 'fullchain.pem')),
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
