import uuid

from backend.helper_client import HelperClient
from tests.api_support import ApiTestCase
from tests.fixture_values import MAILBOX_PASSWORD
from tests.protocol_peer_support import ProtocolPeer


class HelperVersionApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_session(self.fixture.login())
        path = self.fixture.root / 'version-peer.sock'
        self.peer = ProtocolPeer(path)
        self.addCleanup(self.peer.close)
        self.fixture.services.mailboxes.helper = HelperClient(path, 2)

    async def test_incompatible_wire_versions_cannot_authorize_inventory_or_commit_a_mutation(self):
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)
        payload = {'request_id': str(uuid.uuid4()), 'email': 'version@rsalehin24.me',
                   'password': MAILBOX_PASSWORD}
        for version in (99, True, 1.0, None, '1'):
            self.peer.protocol = version
            with self.subTest(protocol_type=type(version).__name__, value=version):
                result = await self.client.get('/api/accounts')
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()['error'], 'incompatible_helper_protocol')
                result = await self.client.post('/api/accounts/create', json=payload)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()['error'], 'incompatible_helper_protocol')
                self.assertEqual(result.json()['operation_id'], payload['request_id'])
                self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertTrue(all(request['protocol'] == 1 for request in self.peer.requests))
        self.assertEqual(self.fixture.host.maddy.calls, [])
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
        self.peer.protocol = 1
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)

    async def test_valid_boolean_denial_remains_a_safe_root_helper_error(self):
        self.peer.ok = False
        result = await self.client.get('/api/accounts')
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()['error'], 'helper_access_denied')
        self.assertEqual(result.headers['cache-control'], 'no-store')
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
        self.peer.ok = True
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)

    async def test_non_boolean_success_flags_cannot_commit_mailbox_metadata(self):
        for flag in (1, 'false', [], {}, None):
            self.peer.ok = flag
            payload = {'request_id': str(uuid.uuid4()), 'email': 'flag@rsalehin24.me',
                       'password': MAILBOX_PASSWORD}
            with self.subTest(flag_type=type(flag).__name__):
                result = await self.client.post('/api/accounts/create', json=payload)
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()['error'], 'invalid_helper_response')
                self.assertEqual(result.json()['operation_id'], payload['request_id'])
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
        self.assertEqual(self.fixture.host.maddy.calls, [])
