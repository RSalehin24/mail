import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from host import cli
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.alert_support import AlertDeliveryFixture


class HostAlertDeliveryTests(AlertDeliveryFixture):
    def setUp(self):
        super().setUp()
        self.paths = {Path('/var/lib/mail-admin-backup/manifest.json'):
                      self.state.root / 'manifest.json'}
        self.monitor.configuration = {'inputs': {'disk_free_minimum_bytes': 1,
            'inode_free_minimum': 1, 'recovery_point_seconds': 93600}}
        self.filesystem = os.statvfs
        self.capacity_root = self.state.root
        if self.filesystem(self.capacity_root).f_files == 0:
            # Btrfs has dynamically allocated inodes and reports no fixed pool.
            # Exercise thresholds on an actual bounded tmpfs inode pool instead.
            capacity = tempfile.TemporaryDirectory(prefix='mail-admin-capacity-', dir='/dev/shm')
            self.addCleanup(capacity.cleanup)
            self.capacity_root = Path(capacity.name)
        self.capacity = self.filesystem(self.capacity_root)
        self.write_healthy()

    def write_healthy(self):
        atomic_json(self.paths[Path('/var/lib/mail-admin-backup/manifest.json')], {
            'verified': True, 'available': True, 'availability_observed_at': self.state.now,
            'cutoff': datetime.fromtimestamp(self.state.now - 1, UTC).isoformat()})

    def fixed_state(self, path):
        return protected_json(self.paths[path], os.getuid())

    def actual_capacity(self, path):
        self.assertEqual(path, '/opt/maddy/data')
        return self.filesystem(self.capacity_root)

    def host_scope(self):
        scope = self.delivery_scope()
        scope.enter_context(patch('host.monitor.protected_json', side_effect=self.fixed_state))
        scope.enter_context(patch('host.monitor.os.statvfs', side_effect=self.actual_capacity))
        return scope

    def test_submicrosecond_clock_keeps_a_healthy_backup_cutoff_in_the_past(self):
        self.state.now = float(int(self.state.now)) + 0.0000007
        self.write_healthy()
        manifest = self.paths[Path('/var/lib/mail-admin-backup/manifest.json')]
        cutoff = datetime.fromisoformat(protected_json(manifest, os.getuid())['cutoff'])
        age = self.state.now - cutoff.timestamp()
        self.assertGreater(age, 0)
        self.assertLess(age, 2)
        with self.host_scope():
            self.monitor.host()
        self.assert_received('recovered', 2)

    def fail_delivery_with_host_faults(self):
        manifest, = self.paths.values()
        record = protected_json(manifest, os.getuid())
        record['cutoff'] = datetime.fromtimestamp(self.state.now - 93601, UTC).isoformat()
        atomic_json(manifest, record)
        inputs = self.monitor.configuration['inputs']
        inputs['disk_free_minimum_bytes'] = self.capacity.f_blocks * self.capacity.f_frsize + 1
        self.smtp.reject_auth = True
        with self.assertRaisesRegex(ServiceError, 'notifications_pending'):
            self.monitor.host()
        failed = protected_json(self.alerts.path, os.getuid())
        self.assertTrue(all(not record['delivered'] for record in failed.values()))
        return failed

    def recover_pending_host_notifications(self, failed):
        self.monitor.configuration['inputs']['disk_free_minimum_bytes'] = 1
        self.write_healthy()
        with self.assertRaisesRegex(ServiceError, 'notifications_pending'):
            self.monitor.host()
        self.smtp.reject_auth = False
        self.state.now = max(record['next_attempt'] for record in failed.values())
        self.alerts.flush()
        self.monitor.host()

    def assert_condition_order(self, names):
        for name in names:
            messages = [message for message in self.smtp.messages if name.encode() in message]
            self.assertEqual(len(messages), 3)
            self.assertNotIn(b'"condition": "recovered"', messages[1])
            self.assertIn(b'recovered', messages[2])

    def test_missed_backup_and_capacity_alerts_retry_without_dashboard(self):
        with self.host_scope():
            self.monitor.host()
            self.assert_received('recovered', 2)
            failed = self.fail_delivery_with_host_faults()
            self.recover_pending_host_notifications(failed)
        self.assert_received('failed_or_missed', 1)
        self.assert_received('low', 1)
        self.assert_received('recovered', 4)
        self.assertEqual(len(self.smtp.messages), 6)
        self.assert_condition_order(failed)

    def serve_expiring_certificates(self, days):
        served = {'expiry': self.state.now + days * 86400, 'served_sha256': 'a' * 64}
        with patch('host.monitor.served_certificate', return_value=served):
            self.monitor.external()

    def test_actual_free_inode_threshold_is_delivered_and_recovers(self):
        with self.host_scope():
            self.monitor.host()
            inputs = self.monitor.configuration['inputs']
            inputs['inode_free_minimum'] = self.capacity.f_files + 1
            self.monitor.host()
            self.assert_received('low', 1)
            inputs['inode_free_minimum'] = 1
            self.monitor.host()
        self.assert_received('recovered', 3)
        self.assertEqual(len(self.smtp.messages), 4)

    def test_served_certificate_warning_windows_are_delivered_for_both_endpoints(self):
        with self.delivery_scope():
            for days in (60, 21, 7, 60):
                self.serve_expiring_certificates(days)
        self.assert_received('warning_21_days', 2)
        self.assert_received('critical_7_days', 2)
        self.assert_received('recovered', 4)
        self.assertEqual(len(self.smtp.messages), 8)

    def assert_cli_failure(self, failure):
        context = SimpleNamespace(alerts=self.alerts)
        with (patch('host.cli.os.geteuid', return_value=0),
              patch('sys.argv', ['mail-admin-host', 'backup']),
              patch('host.cli.HostContext.load', return_value=context),
              patch('host.cli.run', side_effect=failure) as action):
            self.assertEqual(cli.main(), 1)
            action.assert_called_once_with('backup', context)

    def assert_cli_recovery(self):
        with (patch('host.cli.os.geteuid', return_value=0),
              patch('sys.argv', ['mail-admin-host', 'backup']),
              patch('host.cli.HostContext.load', return_value=SimpleNamespace(alerts=self.alerts)),
              patch('host.cli.run')):
            self.assertEqual(cli.main(), 0)
            self.assertEqual(cli.main(), 0)

    def test_cli_reports_failed_or_deferred_work_and_recovery_through_the_independent_outbox(self):
        for failure in (OSError('synthetic failed capture'),
                        ServiceError('helper_reconciliation_required', 409)):
            with self.subTest(failure=type(failure).__name__), self.delivery_scope():
                self.assert_cli_failure(failure)
                self.assert_cli_recovery()
        self.assert_received('failed_or_deferred', 2)
        self.assert_received('recovered', 2)
        self.assertEqual(len(self.smtp.messages), 4)
        self.assertNotIn('synthetic failed capture', json.dumps(
            protected_json(self.alerts.path, os.getuid())))
