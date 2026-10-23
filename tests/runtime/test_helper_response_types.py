import unittest
import uuid

from tests.fixture_values import MAILBOX_PASSWORD
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class VersionReply:
    def __init__(self):
        self.protocol, self.ok = 1, True

    def handle(self, request):
        return {'protocol': self.protocol, 'ok': self.ok, 'request_id': request.request_id,
                'credentials': [], 'storage': [], 'state': 'complete'}


class RuntimeHelperResponseTests(unittest.TestCase):
    def setUp(self):
        self.reply = VersionReply()
        self.fixture = RuntimeFixture(self.reply)
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        authentication = RuntimeAuthentication(self, self.fixture)
        authentication.enroll()
        authentication.login()
        self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)

    def assert_mutation_rejected(self, expected):
        payload = {'request_id': str(uuid.uuid4()), 'email': 'typed-reply@rsalehin24.me',
                   'password': MAILBOX_PASSWORD}
        status, headers, body = self.fixture.api('POST', '/api/accounts/create', payload)
        self.assertEqual(status, 503)
        self.assertEqual(body['error'], expected)
        self.assertEqual(body['operation_id'], payload['request_id'])
        self.assertEqual(headers['cache-control'], 'no-store')
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
        self.assertEqual(self.fixture.host.maddy.calls, [])

    def test_actual_https_runtime_rejects_non_integer_and_incompatible_wire_versions(self):
        for version in (True, 1.0, '1', None, 99):
            with self.subTest(protocol_type=type(version).__name__):
                self.reply.protocol = version
                self.assert_mutation_rejected('incompatible_helper_protocol')
        self.reply.protocol = 1
        self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)

    def test_actual_https_runtime_rejects_non_boolean_success_flags_without_false_commits(self):
        for flag in (1, 'false', [], {}, None):
            with self.subTest(flag_type=type(flag).__name__):
                self.reply.ok = flag
                self.assert_mutation_rejected('invalid_helper_response')
        self.reply.ok = True
        self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)
