import tempfile
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

from helper.recovery_records import QUEUE_LIMIT, RecoveryRecords
from host.mailbox_recovery import MailboxRecovery
from shared.errors import ServiceError


def enqueue(records, email, kind='recovery_delete'):
    identifier = str(uuid.uuid4())
    records.enqueue(identifier, kind, identifier, email, {'confirmation': email}, 1000)
    return identifier


class RecoveryQueueTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.records = RecoveryRecords(Path(self.directory.name) / 'recovery.db')

    def test_different_mailboxes_wait_in_line_without_blocking_each_other(self):
        first = enqueue(self.records, 'one@rsalehin24.me')
        second = enqueue(self.records, 'two@rsalehin24.me')
        self.assertEqual(self.records.job(first)['state'], 'queued')
        self.assertEqual(self.records.job(second)['state'], 'queued')

    def test_one_mailbox_never_has_two_active_jobs(self):
        enqueue(self.records, 'one@rsalehin24.me')
        with self.assertRaisesRegex(ServiceError, 'recovery_operation_in_progress'):
            enqueue(self.records, 'one@rsalehin24.me')

    def test_a_stuck_job_does_not_block_other_mailboxes(self):
        stuck = enqueue(self.records, 'stuck@rsalehin24.me', 'recovery_remove')
        self.assertEqual(self.records.claim(1001)['id'], stuck)  # now running, never finished
        self.assertEqual(self.records.job(enqueue(self.records, 'other@rsalehin24.me'))['state'],
                         'queued')

    def test_queue_is_bounded(self):
        for number in range(QUEUE_LIMIT):
            enqueue(self.records, f'user{number}@rsalehin24.me')
        with self.assertRaisesRegex(ServiceError, 'recovery_queue_full'):
            enqueue(self.records, 'extra@rsalehin24.me')

    def test_same_request_id_is_still_idempotent(self):
        identifier = enqueue(self.records, 'one@rsalehin24.me')
        again = self.records.enqueue(identifier, 'recovery_delete', identifier,
                                     'one@rsalehin24.me', {'confirmation': 'one@rsalehin24.me'},
                                     2000)
        self.assertEqual(again['request_id'], identifier)


class WorkerDrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.worker = object.__new__(MailboxRecovery)
        self.worker.root = Path(self.directory.name)
        self.worker.records = RecoveryRecords(self.worker.root / 'recovery.db')
        self.worker._expire = MagicMock()

    def run_worker(self):
        with patch('host.mailbox_recovery.HostLock', return_value=nullcontext()):
            self.worker.run()

    def test_one_run_finishes_every_queued_job_in_order(self):
        ids = [enqueue(self.worker.records, f'user{n}@rsalehin24.me') for n in range(3)]
        performed = []
        self.worker._perform = lambda job: performed.append(job['id'])
        self.run_worker()
        self.assertEqual(performed, ids)
        self.assertTrue(all(self.worker.records.job(i)['state'] == 'complete' for i in ids))
        self.worker._expire.assert_called_once()

    def test_a_failed_job_stops_the_run_and_later_jobs_wait_for_the_next_run(self):
        ids = [enqueue(self.worker.records, f'user{n}@rsalehin24.me') for n in range(3)]

        def perform(job):
            if job['id'] == ids[1]:
                raise ServiceError('maddy_unavailable', 503)
        self.worker._perform = perform
        self.run_worker()
        states = [self.worker.records.job(i)['state'] for i in ids]
        self.assertEqual(states[0], 'complete')
        self.assertIn(states[1], {'failed', 'reconciliation_required'})
        self.assertEqual(states[2], 'queued')
        self.worker._expire.assert_not_called()

    def test_run_stops_taking_new_jobs_after_the_time_budget(self):
        ids = [enqueue(self.worker.records, f'user{n}@rsalehin24.me') for n in range(3)]
        self.worker._perform = MagicMock()
        with patch('host.mailbox_recovery.DRAIN_BUDGET_SECONDS', 0):
            self.run_worker()
        self.worker._perform.assert_not_called()
        self.assertTrue(all(self.worker.records.job(i)['state'] == 'queued' for i in ids))
