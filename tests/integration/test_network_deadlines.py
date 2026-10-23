import asyncio
import socket
import time
from unittest.mock import patch

from backend.dns import observe_mx
from backend.helper_client import HelperClient
from backend.relay import Relay, RelayDependencies, SmtpTransport
from tests.api_support import ApiTestCase
from tests.silent_peer_support import SilentPeer


class NetworkDeadlineApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)

    def peer(self, transport):
        peer = SilentPeer(transport)
        self.addCleanup(peer.close)
        return peer

    async def assert_health_responsive(self):
        started = time.monotonic()
        result = await self.client.get('/api/status')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'status': 'ok'})
        self.assertLess(time.monotonic() - started, 1)

    async def test_stalled_real_smtp_times_out_without_enabling_otp_or_blocking_health(self):
        peer, connect = self.peer('tcp'), socket.create_connection

        def route(endpoint, *arguments, **keywords):
            self.assertEqual(endpoint, ('smtp-relay.brevo.com', 587))
            return connect(peer.address, *arguments, **keywords)

        self.fixture.services.relay = Relay(RelayDependencies(self.fixture.database,
            self.fixture.keys, SmtpTransport(2), lambda: self.fixture.now))
        self.fixture.services.relay.save({'host': 'smtp-relay.brevo.com', 'port': 587,
            'tls_mode': 'starttls', 'username': 'synthetic', 'credential': 'fixture-secret'})
        with patch('socket.create_connection', side_effect=route):
            started = time.monotonic()
            request = asyncio.create_task(self.client.post('/api/settings/relay/test',
                                                          json={'mode': 'delivery'}))
            self.assertTrue(await asyncio.to_thread(peer.received.wait, 1))
            await self.assert_health_responsive()
            result = await request
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()['connection_ok'])
        self.assertFalse(result.json()['smtp_accepted'])
        self.assertFalse(self.fixture.services.relay.otp_ready())

    async def test_stalled_udp_dns_and_full_queues_preserve_deadlines_and_health(self):
        peer, connect = self.peer('udp'), socket.socket.connect

        def route(connection, endpoint):
            self.assertEqual(endpoint, ('127.0.0.1', 53))
            self.assertEqual(connection.type, socket.SOCK_DGRAM)
            return connect(connection, peer.address)

        self.fixture.services.dns = observe_mx
        with (patch('backend.dns._resolver', return_value='127.0.0.1'),
              patch('socket.socket.connect', new=route)):
            started = time.monotonic()
            first = asyncio.create_task(self.client.get('/api/diagnostics'))
            self.assertTrue(await asyncio.to_thread(peer.received.wait, 1))
            excess = [asyncio.create_task(self.client.get('/api/diagnostics')) for _ in range(6)]
            await self.assert_health_responsive()
            result = await first
            overloaded = await asyncio.gather(*excess)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['dns_mx']['state'], 'unknown')
        self.assertLess(time.monotonic() - started, 3)
        refused = [response for response in overloaded if response.status_code == 503]
        self.assertTrue(refused)
        self.assertTrue(all(response.json()['error'] == 'work_overloaded' for response in refused))
        self.assertTrue(all(response.headers['retry-after'] == '1' for response in refused))

    async def test_stalled_unix_helper_fails_safely_without_blocking_health(self):
        path = self.fixture.root / 'silent-helper.sock'
        peer = SilentPeer('unix', str(path))
        self.addCleanup(peer.close)
        self.fixture.services.mailboxes.helper = HelperClient(path, 2)
        started = time.monotonic()
        request = asyncio.create_task(self.client.get('/api/accounts'))
        self.assertTrue(await asyncio.to_thread(peer.received.wait, 1))
        await self.assert_health_responsive()
        result = await request
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['error'], 'helper_unavailable')
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(self.fixture.host.maddy.calls, [])
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
