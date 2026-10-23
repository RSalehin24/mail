import json
import os
import unittest
from unittest.mock import patch

from helper.status import _observation
from shared.files import protected_json
from tests.support import StateFixture


class HostStatusTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.path = self.fixture.root / 'host-state.json'

    def observe(self, record):
        self.path.write_text(json.dumps(record))
        self.path.chmod(0o600)
        with patch('helper.status.protected_json',
                   side_effect=lambda path: protected_json(path, os.getuid())):
            return _observation(self.path, self.fixture.now)

    def test_nested_consumers_never_expose_unallowlisted_host_material(self):
        consumer = {'intent': 'expected_running', 'state': 'active',
                    'active_generation': 'a' * 64, 'served_sha256': 'b' * 64,
                    'served_expiry': self.fixture.now + 86400,
                    'private_key': 'fixture-protected-key',
                    'credential': 'fixture-protected-credential'}
        result = self.observe({'observed_at': self.fixture.now, 'outcome': 'active',
                               'consumers': {'maddy': consumer, 'unexpected': consumer},
                               'credential': 'fixture-protected-credential'})
        self.assertEqual(set(result['consumers']), {'maddy'})
        self.assertEqual(set(result['consumers']['maddy']),
                         {'intent', 'state', 'active_generation', 'served_sha256', 'served_expiry'})
        self.assertFalse('fixture-protected' in json.dumps(result),
                         'Host summary leaked private data')

    def test_bad_timestamp_types_are_unknown_instead_of_breaking_the_status_operation(self):
        for value in (True, 'recent', [], {}, None):
            with self.subTest(kind=type(value).__name__):
                self.assertEqual(self.observe({'observed_at': value, 'outcome': 'active'}),
                                 {'state': 'unknown', 'reason': 'unavailable'})

    def test_nested_values_in_scalar_fields_cannot_be_forwarded_as_diagnostics(self):
        for field in ('outcome', 'expiry', 'verified', 'cutoff'):
            with self.subTest(field=field):
                result = self.observe({'observed_at': self.fixture.now,
                                       field: {'credential': 'fixture-protected-credential'}})
                self.assertEqual(result, {'state': 'unknown', 'reason': 'unavailable'})

    def test_invalid_consumer_shapes_are_unknown_and_valid_backup_fields_remain_compatible(self):
        for consumers in ([], None, {'maddy': []}, {'maddy': {'state': {'secret': 'fixture'}}}):
            with self.subTest(kind=type(consumers).__name__):
                self.assertEqual(self.observe({'observed_at': self.fixture.now,
                                               'consumers': consumers}),
                                 {'state': 'unknown', 'reason': 'unavailable'})
        record = {'availability_observed_at': self.fixture.now, 'verified': True,
                  'available': True, 'cutoff': '2027-01-15T08:00:00+00:00'}
        self.assertEqual(self.observe(record), record)

    def test_missing_future_and_stale_observations_stay_unknown_at_the_exact_boundary(self):
        for observed in (0, self.fixture.now + 1, self.fixture.now - 300.001):
            with self.subTest(observed=observed):
                self.assertEqual(self.observe({'observed_at': observed, 'outcome': 'active'}),
                                 {'state': 'unknown', 'reason': 'stale'})
        record = {'observed_at': self.fixture.now - 300, 'outcome': 'active'}
        self.assertEqual(self.observe(record), record)
