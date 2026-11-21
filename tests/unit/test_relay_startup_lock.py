import tempfile
import unittest
from pathlib import Path

from helper.dependencies import interrupted_relay_change


class InterruptedRelayChangeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)

    def test_a_clean_data_directory_needs_no_lock_to_start(self):
        (self.data / "submission-relay.conf").touch()
        self.assertFalse(interrupted_relay_change(self.data))

    def test_a_leftover_rollback_or_pending_record_needs_the_lock(self):
        for name in ("submission-relay.rollback", "submission-relay.pending.json"):
            with self.subTest(name=name):
                leftover = self.data / name
                leftover.touch()
                self.assertTrue(interrupted_relay_change(self.data))
                leftover.unlink()
