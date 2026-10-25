"""Observe real absent/running/stopped dashboard containers before baseline admission."""

import unittest
import uuid

from host.backup import FULL_STATE_SOURCES
from host.baseline import BaselineServices
from shared.errors import ServiceError
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class BaselineWeb(RuntimeFixture):
    def __init__(self):
        self.name = 'mail-admin-baseline-fixture-' + str(uuid.uuid4())
        super().__init__()

    def command(self, arguments):
        values = list(arguments)
        if values[0] == 'create':
            values[values.index('--name') + 1] = self.name
        return super().command(tuple(values))


class BaselineDocker:
    def __init__(self, web):
        self.web = web
        self.executable = '/usr/bin/docker'

    def run(self, arguments):
        expected = ('ps', '--all', '--filter', 'name=^/mail-admin$', '--format', '{{.Names}}')
        if arguments != expected:
            raise RuntimeError('Only the private baseline fixture may be observed')
        actual = self.web.command((
            'ps', '--all', '--filter', 'name=^/' + self.web.name + '$', '--format', '{{.Names}}',
        )).stdout.strip()
        if actual not in {'', self.web.name}:
            raise RuntimeError('Unexpected baseline fixture identity')
        return 'mail-admin' if actual else ''


class BaselineSourceTests(unittest.TestCase):
    def services(self, names):
        deployment = {'inputs': {'operation_timeout_seconds': 5}, 'baseline_maddy_mounts': {}}
        return BaselineServices(BaselineDocker(self.web), deployment, names)

    def test_initial_install_and_running_or_stopped_upgrades_enforce_actual_state_sources(self):
        self.web = BaselineWeb()
        self.addCleanup(self.web.close)
        self.services({'maddy', 'configuration', 'tls'}).require_existing_dashboard_sources()
        self.web.start()
        authentication = RuntimeAuthentication(self, self.web)
        authentication.enroll()
        authentication.login()
        for missing in FULL_STATE_SOURCES:
            with self.subTest(missing=missing), self.assertRaisesRegex(
                ServiceError, 'baseline_dashboard_sources_incomplete',
            ):
                self.services(FULL_STATE_SOURCES - {missing}).require_existing_dashboard_sources()
        self.assertEqual(self.web.api('GET', '/api/accounts')[0], 200)
        self.services(FULL_STATE_SOURCES).require_existing_dashboard_sources()
        self.web.command(('stop', '--time', '30', self.web.identifier))
        with self.assertRaisesRegex(ServiceError, 'baseline_dashboard_sources_incomplete'):
            self.services({'maddy'}).require_existing_dashboard_sources()
