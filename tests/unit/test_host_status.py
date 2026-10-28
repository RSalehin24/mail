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

    def test_old_observations_retain_redacted_values_and_the_original_timestamp(self):
        observed = self.fixture.now - 301
        result = self.observe({'observed_at': observed, 'outcome': 'active',
                               'credential': 'private', 'verified': True})
        self.assertEqual(result, {'observed_at': observed, 'outcome': 'active',
                                  'verified': True, 'state': 'stale', 'reason': 'stale'})
        for invalid in (0, self.fixture.now + 1):
            self.assertEqual(self.observe({'observed_at': invalid, 'outcome': 'active'}),
                             {'state': 'unknown', 'reason': 'invalid_observation_time'})
        record = {'observed_at': self.fixture.now - 300, 'outcome': 'active'}
        self.assertEqual(self.observe(record), record)

    def test_thresholds_expose_only_configured_numeric_limits(self):
        from helper.status import _thresholds
        inputs = {'disk_free_minimum_bytes': 1024, 'inode_free_minimum': 10,
                  'recovery_point_seconds': 93600, 'credential': 'private'}
        with patch('helper.status.protected_json', return_value={'inputs': inputs}):
            result = _thresholds()
        self.assertEqual(set(result), {'disk_free_minimum_bytes', 'inode_free_minimum',
                                       'recovery_point_seconds'})
        for value in (True, -1, '1024', None):
            with self.subTest(value=value), patch('helper.status.protected_json', return_value={
                'inputs': {**inputs, 'disk_free_minimum_bytes': value},
            }):
                self.assertEqual(_thresholds(), {'state': 'unavailable'})


class CpuSamplerTests(unittest.TestCase):
    def test_shared_deltas_missing_reset_and_stale(self):
        from helper.status import CpuSampler
        sampler = CpuSampler()
        def reading(total, idle):
            return {'cpu_total_ticks': total, 'cpu_idle_ticks': idle, 'cpu_logical_count': 2}
        with patch('helper.status.time.monotonic', side_effect=[0, 1, 1.5, 2, 3, 4, 8]), \
                patch('helper.status.time.time', return_value=123), \
                patch('helper.status._cpu_ticks', side_effect=[
                    reading(100, 50), reading(200, 75), {}, reading(20, 10),
                    reading(120, 60), reading(220, 110),
                ]) as read:
            self.assertNotIn('cpu_utilization_percent', sampler.read())
            sample = sampler.read()
            self.assertEqual(sample['cpu_utilization_percent'], 75)
            self.assertEqual(sample['cpu_sample_interval_seconds'], 1)
            self.assertEqual(sample['cpu_sampled_at'], 123)
            self.assertEqual(sampler.read(), sample)
            self.assertEqual(read.call_count, 2)
            self.assertNotIn('cpu_utilization_percent', sampler.read())
            self.assertNotIn('cpu_utilization_percent', sampler.read())
            self.assertEqual(sampler.read()['cpu_utilization_percent'], 50)
            self.assertNotIn('cpu_utilization_percent', sampler.read())

    def test_counter_reset_reestablishes_baseline(self):
        from helper.status import CpuSampler
        sampler = CpuSampler()
        with patch('helper.status.time.monotonic', side_effect=[0, 1, 2]), \
                patch('helper.status._cpu_ticks', side_effect=[
                    {'cpu_total_ticks': total, 'cpu_idle_ticks': idle, 'cpu_logical_count': 1}
                    for total, idle in [(100, 50), (20, 10), (120, 60)]
                ]):
            sampler.read()
            self.assertNotIn('cpu_utilization_percent', sampler.read())
            self.assertEqual(sampler.read()['cpu_utilization_percent'], 50)
