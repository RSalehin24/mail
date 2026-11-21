import os
import tempfile
import threading
import unittest
from pathlib import Path

from backend.certificate_watch import restart_when_replaced

POLL_SECONDS = 0.01
WAIT_SECONDS = 5


class CertificateWatchTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.certificate = Path(directory.name) / "certificate.crt"
        self.certificate.write_text("old")
        self.restarted = threading.Event()

    def test_a_replaced_certificate_triggers_one_restart(self):
        thread = restart_when_replaced(self.certificate, POLL_SECONDS, self.restarted.set)
        self.assertFalse(self.restarted.wait(0.1))
        replacement = self.certificate.with_suffix(".new")
        replacement.write_text("new")
        os.replace(replacement, self.certificate)
        self.assertTrue(self.restarted.wait(WAIT_SECONDS))
        thread.join(WAIT_SECONDS)
        self.assertFalse(thread.is_alive())

    def test_an_unchanged_certificate_never_restarts(self):
        restart_when_replaced(self.certificate, POLL_SECONDS, self.restarted.set)
        self.assertFalse(self.restarted.wait(0.2))
