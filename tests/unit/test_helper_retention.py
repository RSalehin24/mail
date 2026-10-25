import unittest

from host.locks import HostLock
from shared.errors import ServiceError
from tests.helper_support import HelperFixture

HISTORY_SECONDS = 90 * 86400


class HelperRetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)

    def recorded(self, age):
        request = self.fixture.request('create', 'retained-log@rsalehin24.me')
        self.fixture.journal.begin(request, (self.fixture.now - age, {}))
        self.fixture.journal.update(request.request_id, {'state': 'complete'})
        return request.request_id

    def test_live_helper_inventory_expires_old_terminal_records_and_keeps_the_exact_boundary(self):
        expired = self.recorded(HISTORY_SECONDS + 1)
        current = self.recorded(HISTORY_SECONDS)
        self.fixture.helper.handle(self.fixture.request('inventory'))
        self.assertIsNone(self.fixture.journal.lookup(expired))
        self.assertEqual(self.fixture.journal.lookup(current)['state'], 'complete')

    def test_exclusion_failure_cannot_run_journal_cleanup_outside_the_maintenance_lock(self):
        expired = self.recorded(HISTORY_SECONDS + 1)
        policy = self.fixture.helper.dependencies.lock_policy
        with HostLock(self.fixture.lock, policy):
            with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
                self.fixture.helper.handle(self.fixture.request('inventory'))
        self.assertIsNotNone(self.fixture.journal.lookup(expired))
        self.fixture.helper.handle(self.fixture.request('inventory'))
        self.assertIsNone(self.fixture.journal.lookup(expired))
