import tempfile
import unittest
import uuid
from pathlib import Path

from helper.recovery_records import RecoveryRecords
from helper.retention import RecoveryPolicy
from shared.errors import ServiceError


class RecoveryRecordsTests(unittest.TestCase):
    def test_verified_point_required_and_forever_excluded_from_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            records = RecoveryRecords(Path(directory) / 'recovery.db')
            policy = RecoveryPolicy(1, True, False, '7_days')
            for choice in ('7_days', '30_days', 'forever'):
                identifier = str(uuid.uuid4())
                record = records.begin(identifier, 'cloud@rsalehin24.me', policy,
                                       choice, 1000, 'Cloud', 'Preserve notes')
                self.assertEqual(record['notes'], 'Preserve notes')
                with self.assertRaises(ServiceError):
                    records.transition(identifier, 'capturing', 'deleting', 1001)
                records.transition(identifier, 'capturing', 'deleting', 1001,
                                   verified_snapshot='root-verified-snapshot-' + choice)
                records.transition(identifier, 'deleting', 'deleted', 1002)
            self.assertEqual(len(records.pinned_snapshots()), 3)
            self.assertEqual(len(records.expired(1000 + 7 * 86400)), 1)
            self.assertEqual(len(records.expired(1000 + 30 * 86400)), 2)
            self.assertEqual(len(records.expired(2**62)), 2)
            self.assertEqual(RecoveryRecords(records.path).expired(2**62),
                             records.expired(2**62))

    def test_interrupted_mutations_block_new_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            records = RecoveryRecords(Path(directory) / 'recovery.db')
            policy = RecoveryPolicy(1, True, False, '7_days')
            identifier = str(uuid.uuid4())
            records.begin(identifier, 'cloud@rsalehin24.me', policy,
                          '7_days', 1000, '', '')
            with self.assertRaises(ServiceError):
                records.begin(str(uuid.uuid4()), 'other@rsalehin24.me', policy,
                              '7_days', 1001, '', '')
            records.transition(identifier, 'capturing', 'reconciliation_required', 1002)
            self.assertEqual(records.begin(identifier, 'cloud@rsalehin24.me', policy,
                                           '7_days', 1000, '', '')['state'],
                             'reconciliation_required')
            with self.assertRaises(ServiceError):
                records.begin(identifier, 'other@rsalehin24.me', policy,
                              '7_days', 1000, '', '')


    def test_opening_current_ledger_does_not_rewrite_backup_state(self):
        with tempfile.TemporaryDirectory() as directory:
            records = RecoveryRecords(Path(directory) / 'recovery.db')
            before = records.path.stat().st_mtime_ns
            RecoveryRecords(records.path)
            self.assertEqual(records.path.stat().st_mtime_ns, before)
