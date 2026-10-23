"""Exercise actual OpenSSL validation on the Linux certificate deployment boundary."""

import shutil
import unittest

from host.certificates import PairValidator
from shared.errors import ServiceError
from tests.support import StateFixture
from tests.tls_support import CertificateFixture


class CertificateValidationTests(unittest.TestCase):
    def test_invalid_mismatched_wrong_host_expired_and_untrusted_complete_pairs_are_rejected(self):
        state = StateFixture()
        self.addCleanup(state.close)
        certificates = CertificateFixture(state.root)
        validator = PairValidator(certificates.trust)
        valid = certificates.issue('valid')
        original_leaf = (valid / 'fullchain.pem').read_bytes()
        other = certificates.issue('different-key')
        shutil.copy2(other / 'privkey.pem', valid / 'privkey.pem')
        self.assertEqual((valid / 'fullchain.pem').read_bytes(), original_leaf)
        with self.assertRaisesRegex(ServiceError, 'certificate_key_mismatch'):
            validator.validate(valid)
        invalid = certificates.issue('invalid')
        (invalid / 'fullchain.pem').write_text('invalid fixture PEM')
        untrusted_directory = state.root / 'untrusted-ca'
        untrusted_directory.mkdir(mode=0o700)
        untrusted = CertificateFixture(untrusted_directory).issue()
        sources = (invalid, certificates.issue('wrong-host', 'wrong.example'),
                   certificates.issue('expired', expired=True), untrusted)
        for source in sources:
            with self.subTest(source=source.name), self.assertRaisesRegex(
                ServiceError, 'certificate_validation_failed'
            ):
                validator.validate(source)
        self.assertIn('served_sha256', validator.validate(other))
