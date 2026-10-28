import asyncio
import dataclasses
import threading
import uuid
from unittest.mock import patch

from tests.api_support import ApiTestCase
from tests.helper_support import MAILBOX_PASSWORD


class LiveTelemetryAndHistoryTests(ApiTestCase):
    async def test_session_inspection_is_authenticated_and_independent_of_diagnostics(self):
        self.assertEqual((await self.client.get('/api/session')).status_code, 401)
        self.set_session(self.fixture.login())
        with patch.object(self.fixture.services, 'dns', side_effect=RuntimeError('slow DNS')), \
                patch.object(self.fixture.host.maddy, 'inventories') as inventory:
            response = await self.client.get('/api/session')
            self.assertEqual(response.json(), {'authenticated': True})
            inventory.assert_not_called()

    async def test_slow_diagnostics_leave_inventory_telemetry_and_logout_responsive(self):
        self.set_session(self.fixture.login())
        entered, release = threading.Event(), threading.Event()

        def slow_dns():
            entered.set()
            release.wait(3)
            return {'state': 'unknown', 'configured_mode': 'direct'}

        self.fixture.services.dns = slow_dns
        diagnostic = asyncio.create_task(self.client.get('/api/diagnostics'))
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            for path in ('/api/accounts', '/api/telemetry', '/api/session'):
                response = await asyncio.wait_for(self.client.get(path), 1)
                self.assertEqual(response.status_code, 200)
            response = await asyncio.wait_for(self.client.post('/api/logout', json={}), 1)
            self.assertEqual(response.status_code, 200)
        finally:
            release.set()
            await diagnostic

    async def test_telemetry_requires_session_and_does_not_run_mail_cli_or_dns(self):
        self.assertEqual((await self.client.get('/api/telemetry')).status_code, 401)
        self.set_session(self.fixture.login())
        measured = {'observed_at': self.fixture.now, 'memory_total_bytes': 1000,
                    'memory_available_bytes': 400, 'scope': 'fixture'}
        helper = self.fixture.host.helper
        helper.dependencies = dataclasses.replace(helper.dependencies, status=lambda: measured)
        with patch.object(self.fixture.host.maddy, 'validate') as validate, \
                patch.object(self.fixture.host.maddy, 'inventories') as inventory, \
                patch.object(self.fixture.services, 'dns') as dns:
            for _ in range(5):
                response = await self.client.get('/api/telemetry')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['summary'], measured)
                self.assertIn('no-store', response.headers['cache-control'])
            validate.assert_not_called()
            inventory.assert_not_called()
            dns.assert_not_called()

    async def test_mailbox_receipts_are_in_details_and_admin_history_excludes_other_senders(self):
        email = 'cloud@rsalehin24.me'
        token = self.fixture.login()
        self.set_session(token)
        self.fixture.services.mailboxes.create({'request_id': str(uuid.uuid4()),
            'email': email, 'password': MAILBOX_PASSWORD})
        relay = self.fixture.services.relay
        relay.save({'host': 'smtp-relay.brevo.com', 'port': 587, 'tls_mode': 'starttls',
                    'username': 'fixture', 'credential': 'synthetic-private-key'})
        cloud = relay.test(email, ('delivery', 'fixture'))
        relay.confirm(cloud['test_id'])
        admin = relay.test('mail@rsalehin24.me', ('delivery', 'fixture'))
        settings = (await self.client.get('/api/settings/relay')).json()
        self.assertEqual([test['id'] for test in settings['tests']], [admin['test_id']])
        self.assertFalse(settings['otp_ready'])
        details = (await self.client.get('/api/accounts/'+email)).json()
        self.assertEqual(details['outbound_readiness'], 'confirmed')
        self.assertEqual(details['sender_history'][0]['id'], cloud['test_id'])
        self.assertNotIn('synthetic-private-key', str(details))

    async def test_current_confirmation_survives_many_tests_and_old_revision_stays_historical(self):
        email = 'cloud@rsalehin24.me'
        self.set_session(self.fixture.login())
        self.fixture.services.mailboxes.create({'request_id': str(uuid.uuid4()),
            'email': email, 'password': MAILBOX_PASSWORD})
        relay = self.fixture.services.relay
        settings = {'host': 'smtp-relay.brevo.com', 'port': 587, 'tls_mode': 'starttls',
                    'username': 'fixture', 'credential': 'synthetic-private-key'}
        relay.save(settings)
        first = relay.test(email, ('delivery', 'fixture'))
        relay.confirm(first['test_id'])
        for _ in range(6):
            with self.fixture.database.transaction() as connection:
                connection.execute('INSERT INTO relay_tests VALUES(?,1,?,?,1,1,0,?)',
                                   (str(uuid.uuid4()), email, 'rsalehin24@gmail.com',
                                    self.fixture.now+1))
        self.assertEqual(self.fixture.services.mailboxes.details(email)['outbound_readiness'],
                         'confirmed')
        relay.save(settings)
        details = self.fixture.services.mailboxes.details(email)
        self.assertEqual(details['outbound_readiness'], 'unverified')
        self.assertTrue(any(test['id'] == first['test_id'] and test['confirmed']
                            for test in details['sender_history']))
        self.assertEqual(details['current_relay_revision'], 2)
