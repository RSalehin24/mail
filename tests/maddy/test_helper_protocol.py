import dataclasses
import json
import time
import unittest
import uuid
from unittest.mock import patch

import helper.maddy
from helper.deletion import DeletionGuard, DeletionPaths
from helper.maddy import LAUNCHER
from helper.server import SocketPolicy
from helper.status import _observation
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.protocol_support import INVALID_ADDRESSES, INVALID_PASSWORDS
from tests.socket_support import SocketFixture


class RealHelperProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        self.socket = SocketFixture(self.fixture.root,
                                    (self.fixture.helper, SocketPolicy(10001, 10001, 30)))
        self.addCleanup(self.socket.close)

    def request(self):
        return {'protocol': 1, 'operation': 'create', 'request_id': str(uuid.uuid4()),
                'email': 'demo_user.library@rsalehin24.me', 'password': MAILBOX_PASSWORD}

    def test_invalid_schema_address_and_password_requests_never_execute_real_cli(self):
        base = self.request()
        variants = [{'protocol': True}, {'protocol': 99}, {'operation': 'exec'}, {'operation': []},
                    {'container': 'another'}, {'path': '/etc/shadow'}, {'command': 'id'},
                    {'shell': '$(id)'}, {'email': '--help@rsalehin24.me'}]
        requests = [{**base, **extra} for extra in variants]
        requests += [{key: value for key, value in base.items() if key != missing}
                     for missing in base]
        requests += [None, [], True, 'not-a-request', {**base, 'request_id': 'invalid'},
                     {**base, 'password': []}, {**base, 'email': 42}]
        requests += [{**base, 'email': value} for value in INVALID_ADDRESSES]
        requests += [{**base, 'password': value} for value in INVALID_PASSWORDS]
        with patch.object(self.fixture.maddy, 'change',
                          wraps=self.fixture.maddy.change) as commands:
            for index, request in enumerate(requests):
                response = self.socket.client(request)
                self.assertFalse(response['ok'], str(index))
                self.assertEqual(response['status'], 400, str(index))
        commands.assert_not_called()
        self.assertEqual(self.fixture.maddy.inventories(), {'credentials': [], 'storage': []})
        self.assertIsNone(self.fixture.helper.journal.lookup(base['request_id']))

    def test_normalized_legacy_address_and_exact_password_use_fixed_argv_and_stdin(self):
        request = {**self.request(), 'email': 'Demo_User.Library@Rsalehin24.Me',
                   'password': '  exact $(id); é fixture password  '}
        observed = []
        execute = helper.maddy.execute

        def record(command):
            observed.append(command)
            return execute(command)

        with patch('helper.maddy.execute', side_effect=record):
            response = self.socket.client(request)
        self.assertTrue(response['ok'])
        self.assertEqual(response['state'], 'complete')
        commands = [command for command in observed if command.arguments[1] == 'exec']
        self.assertTrue(commands)
        for command in commands:
            self.assertEqual(command.arguments[1:5], ('exec', '-i', 'maddy', LAUNCHER))
            self.assertTrue(request['password'] not in command.arguments)
        expected = (request['password'] + '\n').encode() * 2
        self.assertEqual(sum(command.input_bytes == expected for command in commands), 1)
        self.assertTrue(request['password'] not in json.dumps(response))
        self.assertEqual(self.fixture.helper.journal.lookup(request['request_id'])['email'],
                         request['email'].lower())
        with imap_login(self.fixture, request['email'].lower(), request['password']) as connection:
            self.assertEqual(connection.noop()[0], 'OK')
        logs = self.fixture.command(('logs', self.fixture.identifier))
        self.assertTrue(request['password'] not in logs.stdout + logs.stderr)

    def test_request_conflicts_and_unauthorized_peer_cannot_repeat_a_completed_mutation(self):
        request = self.request()
        self.assertEqual(self.socket.client(request)['state'], 'complete')
        with patch.object(self.fixture.maddy, 'change',
                          wraps=self.fixture.maddy.change) as commands:
            repeated = {**request, 'password': ' ignored replacement fixture password '}
            self.assertEqual(self.socket.client(repeated)['state'], 'complete')
            for changed in ({'email': 'another@rsalehin24.me'}, {'operation': 'password'}):
                response = self.socket.client({**repeated, **changed})
                self.assertEqual((response['status'], response['error']),
                                 (409, 'request_id_conflict'))
            denied = self.socket.client(self.request(), 65534)
            self.assertFalse(denied['ok'])
            self.assertIn(denied['error'], {'helper_access_denied', 'peer_disconnected'})
        commands.assert_not_called()
        with imap_login(self.fixture, request['email'], request['password']) as connection:
            self.assertEqual(connection.noop()[0], 'OK')

    def test_host_status_redacts_nested_records_and_tolerates_corrupt_observation_time(self):
        path = self.fixture.root / 'host-observation.json'
        source = {'observed_at': time.time(), 'outcome': 'active',
                  'consumers': {'maddy': {'intent': 'expected_running', 'state': 'active',
                                         'credential': 'fixture-private-material'}}}
        atomic_json(path, source)
        self.fixture.helper.dependencies = dataclasses.replace(
            self.fixture.helper.dependencies, status=lambda: _observation(path, time.time()),
        )
        request = {'protocol': 1, 'operation': 'status', 'request_id': str(uuid.uuid4())}
        response = self.socket.client(request)
        self.assertEqual(set(response), {'ok', 'protocol', 'summary', 'maddy', 'unresolved'})
        self.assertEqual(set(response['maddy']),
                         {'state', 'credential_count', 'storage_count', 'observed_at'})
        self.assertIsInstance(response['maddy']['observed_at'], (int, float))
        self.assertLessEqual(abs(time.time() - response['maddy']['observed_at']), 10)
        self.assertEqual(response['summary']['consumers']['maddy'],
                         {'intent': 'expected_running', 'state': 'active'})
        self.assertTrue('fixture-private' not in json.dumps(response))
        atomic_json(path, {**source, 'observed_at': []})
        response = self.socket.client(request)
        self.assertTrue(response['ok'])
        self.assertEqual(response['summary'], {'state': 'unknown', 'reason': 'unavailable'})
        denied = self.socket.client({**request, 'path': str(path)})
        self.assertEqual(denied['error'], 'invalid_helper_fields')

    def test_missing_malformed_or_disabled_root_deletion_capability_prevents_all_cli(self):
        email = self.request()['email']
        request = {'protocol': 1, 'operation': 'delete', 'request_id': str(uuid.uuid4()),
                   'email': email, 'confirmation': email,
                   'acknowledged': True, 'cutoff': 'untrusted'}
        records = (None, {}, {'inputs': None}, {'inputs': []}, {'inputs': {}},
                   {'inputs': {'permanent_deletion_enabled': False}},
                   {'inputs': {'permanent_deletion_enabled': 1}})
        with patch.object(self.fixture.maddy, 'change',
                          wraps=self.fixture.maddy.change) as commands:
            for index, record in enumerate(records):
                self.assert_deletion_denied((index, record), request)
            injected = self.socket.client({**request, 'permanent_deletion_enabled': True})
            self.assertEqual(injected['error'], 'invalid_helper_fields')
        commands.assert_not_called()
        self.assertIsNone(self.fixture.helper.journal.lookup(request['request_id']))

    def assert_deletion_denied(self, fixture, request):
        index, record = fixture
        deployment = self.fixture.root / ('disabled-deployment-' + str(index) + '.json')
        if record is not None:
            atomic_json(deployment, record)
        guard = DeletionGuard(DeletionPaths(deployment, self.fixture.root / 'unused-policy.json',
                                            self.fixture.root / 'unused-backup.json'), time.time)
        self.fixture.helper.dependencies = dataclasses.replace(
            self.fixture.helper.dependencies, deletion=guard,
        )
        response = self.socket.client(request)
        self.assertFalse(response['ok'])
        self.assertEqual(response['status'], 503 if record is None else 403)
