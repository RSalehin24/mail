import json
import os
import stat
import unittest

from tests.host_reboot_support import (
    container_identities,
    credential_digest,
    process_start_us,
    reboot_intent,
    service_observation,
)
from tests.host_support import HELPER_UNIT, SOCKET, SOCKET_DIRECTORY, GuestHelper
from tests.host_web_support import GuestDashboard


class DashboardLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = GuestHelper.load()
        self.web = GuestDashboard(self.host)
        self.web.ready()

    def test_helper_starts_late_preserving_authorization_and_completed_mutation(self):
        before, credentials = self.host.outcome(), self.host.credentials()
        inode = SOCKET_DIRECTORY.stat().st_ino
        self.host.command(('systemctl', 'stop', HELPER_UNIT))
        try:
            self.assert_late_helper_authorization()
            payload = {'request_id': self.host.record['request_id'],
                       'email': self.host.record['email'],
                       'password': ' ignored retry fixture password '}
            status, _, response = self.web.api(('POST', '/api/accounts/create'), payload)
            self.assertEqual(status, 503)
            self.assertEqual(response['operation_id'], payload['request_id'])
            self.assertIn(response['error'], {'mutation_outcome_unknown', 'operation_pending'})
        finally:
            self.host.command(('systemctl', 'start', HELPER_UNIT))
        self.host.ready()
        status, headers, result = self.web.api(('POST', '/api/accounts/create'), payload)
        self.assertEqual(status, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(self.host.outcome(), before)
        self.assertEqual(self.host.credentials(), credentials)
        self.assertEqual(SOCKET_DIRECTORY.stat().st_ino, inode)

    def assert_late_helper_authorization(self):
        self.assertEqual(self.web.api(('GET', '/api/status'))[0], 200)
        authenticated = self.web.cookie
        self.web.cookie = ''
        try:
            self.assertEqual(self.web.api(('GET', '/api/accounts'))[0], 401)
            status, _, challenge = self.web.api(('POST', '/api/login'), {
                'email': 'rsalehin24@gmail.com', 'password': self.web.material['password'],
            })
            self.assertEqual(status, 200)
            self.assertIn('challenge', challenge)
            self.assertEqual(self.web.api(('GET', '/api/accounts'))[0], 401)
        finally:
            self.web.cookie = authenticated
        status, _, result = self.web.api(('GET', '/api/accounts'))
        self.assertEqual(status, 503)
        self.assertEqual(result['error'], 'helper_unavailable')

    def test_full_reboot_prepares_restricted_paths_before_autostart_and_preserves_outcomes(self):
        before = reboot_intent(self.host)
        self.assertEqual(container_identities(self.host), before['containers'])
        self.assert_boot_order_and_permissions()
        self.assertEqual(self.host.outcome(), before['outcome'])
        self.assertEqual(credential_digest(self.host), before['credential_rows_sha256'])
        self.assertEqual(self.web.api(('GET', '/api/settings/relay'))[0], 200)
        code = "from pathlib import Path; print(Path('/run/mail-admin-helper').stat().st_ino)"
        mounted = int(self.host.command(('docker', 'exec', 'mail-admin', 'python', '-c', code)))
        self.assertEqual(mounted, SOCKET_DIRECTORY.stat().st_ino)
        self.assertEqual(self.host.consumer_inode(), mounted)
        response = self.host.retry_completed()
        self.assertTrue(response['accepted'])
        self.assertEqual(response['result']['state'], 'complete')
        self.assertEqual(credential_digest(self.host), before['credential_rows_sha256'])

    def assert_boot_order_and_permissions(self):
        paths = service_observation(self.host, 'mail-admin-paths.service')
        docker = service_observation(self.host, 'docker.service')
        self.assertEqual(paths['ActiveState'], 'active')
        self.assertEqual(docker['ActiveState'], 'active')
        finished = int(paths['ExecMainExitTimestampMonotonic'])
        self.assertGreater(finished, 0)
        self.assertGreaterEqual(int(docker['ExecMainStartTimestampMonotonic']), finished)
        for path, mode in ((SOCKET_DIRECTORY, 0o750), (SOCKET, 0o660)):
            metadata = path.stat()
            self.assertEqual((stat.S_IMODE(metadata.st_mode), metadata.st_uid, metadata.st_gid),
                             (mode, 0, 10001))
        names = ('maddy', self.host.record['consumer_name'], 'mail-admin')
        tick_us = 1_000_000 // os.sysconf('SC_CLK_TCK')
        for container in json.loads(self.host.command(('docker', 'inspect') + names)):
            self.assertGreaterEqual(process_start_us(container['State']['Pid']), finished - tick_us)
