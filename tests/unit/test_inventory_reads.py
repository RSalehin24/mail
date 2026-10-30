import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from helper.service import HostHelper
from host.locks import LockPolicy
from shared.errors import ServiceError
from tests.helper_support import HelperFixture


class InventoryReadTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        self.helper = self.fixture.helper

    def test_adjacent_status_and_inventory_share_a_validated_observation(self):
        started, release = threading.Event(), threading.Event()
        original = self.fixture.maddy.inventories

        def slow_inventory():
            started.set()
            if not release.wait(2):
                raise AssertionError('Inventory was not released')
            return original()

        self.helper = HostHelper(replace(self.helper.dependencies,
            read_lock_policy=LockPolicy(1, self.helper.dependencies.lock_policy.owner)))
        with (patch.object(self.fixture.maddy, 'validate') as validate,
              patch.object(self.fixture.maddy, 'inventories', side_effect=slow_inventory) as read,
              ThreadPoolExecutor(max_workers=2) as pool):
            first = pool.submit(self.helper.handle, self.fixture.request('inventory'))
            self.assertTrue(started.wait(1))
            second = pool.submit(self.helper.handle, self.fixture.request('status'))
            release.set()
            self.assertEqual(first.result(2)['credentials'], [])
            self.assertEqual(second.result(2)['maddy']['state'], 'available')
            self.assertEqual(read.call_count, 1)
            self.assertEqual(validate.call_count, 1)

    def test_cache_preserves_observation_time_and_does_not_share_mutable_lists(self):
        self.fixture.maddy.credentials.add('fixture@rsalehin24.me')
        first = self.helper.handle(self.fixture.request('inventory'))
        observed = self.fixture.now
        first['credentials'].clear()
        self.fixture.now += 1
        self.assertEqual(self.helper.handle(self.fixture.request('inventory'))['credentials'],
                         ['fixture@rsalehin24.me'])
        status = self.helper.handle(self.fixture.request('status'))['maddy']
        self.assertEqual(status['observed_at'], observed)
        self.assertEqual(status['credential_count'], 1)

    def test_expired_observation_cannot_hide_a_failed_validation(self):
        self.helper.handle(self.fixture.request('inventory'))
        self.fixture.now += 2
        with patch.object(self.fixture.maddy, 'validate',
                          side_effect=ServiceError('maddy_identity_mismatch', 503)):
            with self.assertRaisesRegex(ServiceError, 'maddy_identity_mismatch'):
                self.helper.handle(self.fixture.request('inventory'))
            self.assertEqual(self.helper.handle(self.fixture.request('status'))['maddy']['state'],
                             'unknown')

    def test_mutations_validate_again_and_invalidate_an_adjacent_read(self):
        self.helper.handle(self.fixture.request('inventory'))
        with patch.object(self.fixture.maddy, 'validate',
                          side_effect=ServiceError('maddy_identity_mismatch', 503)):
            with self.assertRaisesRegex(ServiceError, 'maddy_identity_mismatch'):
                self.helper.handle(self.fixture.request('create'))
        self.assertEqual(self.fixture.maddy.calls, [])
        self.assertEqual(self.helper.handle(self.fixture.request('create'))['state'], 'complete')
        result = self.helper.handle(self.fixture.request('inventory'))
        self.assertEqual(result['credentials'], ['fixture@rsalehin24.me'])
        self.assertEqual(result['storage'], ['fixture@rsalehin24.me'])
