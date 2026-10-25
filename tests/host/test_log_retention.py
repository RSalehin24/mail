import unittest

from tests.host_journal_support import (
    JOURNAL_FILE_ALLOWANCE_BYTES,
    JOURNAL_LIMIT_BYTES,
    PrivateJournal,
    retain_observation,
)
from tests.host_support import GuestHelper


class HostLogRetentionTests(unittest.TestCase):
    def test_shipped_journal_limit_expires_old_records_and_keeps_current_records(self):
        journal = PrivateJournal(GuestHelper.load())
        journal.prepare()
        self.addCleanup(journal.close)
        journal.generate()
        observed = journal.observe()
        retain_observation(journal, observed)
        self.assertFalse(observed['old_marker_retained'])
        self.assertTrue(observed['new_marker_retained'])
        self.assertLessEqual(observed['allocated_bytes'],
                             JOURNAL_LIMIT_BYTES + JOURNAL_FILE_ALLOWANCE_BYTES)
