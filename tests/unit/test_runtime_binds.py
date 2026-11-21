import tempfile
import unittest
from pathlib import Path

from backend import binds
from shared.errors import ServiceError

CERTIFICATE = Path("/data/acme/certificates/test-ca/mail.example.test/mail.example.test.crt")
KEY = CERTIFICATE.with_suffix(".key")


class CertificateBindTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for relative in binds.DIRECTORIES:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        for relative in binds.FILES:
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).touch()
        (self.root / CERTIFICATE.relative_to("/")).parent.mkdir(parents=True)
        for path in (CERTIFICATE, KEY):
            (self.root / path.relative_to("/")).touch()

    def test_present_certificate_files_are_accepted(self):
        binds.require_runtime_sources(self.root, (CERTIFICATE, KEY))

    def test_a_missing_certificate_or_key_stops_startup(self):
        for missing in (CERTIFICATE, KEY):
            with self.subTest(missing=missing.name):
                (self.root / missing.relative_to("/")).unlink()
                with self.assertRaises(ServiceError):
                    binds.require_runtime_sources(self.root, (CERTIFICATE, KEY))
                (self.root / missing.relative_to("/")).touch()
