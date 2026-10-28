import io
import logging
import uuid

from backend.offline import OfflineRecovery
from backend.relay import Relay, RelayDependencies, SmtpTransport
from tests.api_support import ApiTestCase
from tests.fixture_values import MAILBOX_PASSWORD
from tests.smtp_support import SmtpFixture


class RelayReadinessApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.peer = SmtpFixture(self.fixture.root / 'smtp')
        self.addCleanup(self.peer.close)
        self.relay = Relay(RelayDependencies(self.fixture.database, self.fixture.keys,
                           SmtpTransport(2), lambda: self.fixture.now))
        self.fixture.services.relay = self.relay
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        self.settings = {'host': 'smtp-relay.brevo.com', 'port': 587,
                         'tls_mode': 'starttls', 'username': 'synthetic',
                         'credential': 'fixture-secret'}
        response = await self.client.post('/api/settings/relay', json=self.settings)
        self.assertEqual(response.status_code, 200)

    async def create(self, email):
        response = await self.client.post('/api/accounts/create', json={
            'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)

    async def delivery(self, route):
        response = await self.client.post(route, json={'mode': 'delivery'})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result['smtp_accepted'])
        self.assertFalse(result['delivery_confirmed'])
        self.assertIn(result['test_id'].encode(), self.peer.messages[-1])
        self.assertIn(('sender', 'FROM:<' + result['sender'] + '>'), self.peer.events)
        self.assertIn(('recipient', 'TO:<rsalehin24@gmail.com>'), self.peer.events)
        self.assertTrue(all(secure for event, secure in self.peer.events
                            if event == 'authentication'))
        return result['test_id']

    async def confirm(self, identifier):
        response = await self.client.post('/api/relay-tests/' + identifier + '/confirm', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'delivery_confirmed': True})

    async def rows(self):
        response = await self.client.get('/api/accounts')
        self.assertEqual(response.status_code, 200)
        return {row['email']: row for row in response.json()}

    async def test_each_sender_needs_its_own_receipt_without_asserting_inbound_or_global_otp(self):
        first, second = 'first@rsalehin24.me', 'second@rsalehin24.me'
        for email in (first, second):
            await self.create(email)
        self.assertTrue(all(row['outbound_readiness'] == 'unverified'
                            for row in (await self.rows()).values()))
        identifier = await self.delivery('/api/accounts/' + first + '/test-sender')
        self.assertEqual((await self.rows())[first]['outbound_readiness'], 'awaiting receipt')
        await self.confirm(identifier)
        rows = await self.rows()
        self.assertEqual(rows[first]['outbound_readiness'], 'confirmed')
        self.assertEqual(rows[second]['outbound_readiness'], 'unverified')
        self.assertFalse(self.relay.otp_ready())
        diagnostics = await self.client.get('/api/diagnostics')
        self.assertEqual(diagnostics.json()['dns_mx']['state'], 'unverified')
        self.assertEqual(diagnostics.json()['relay']['state'], 'configured_unverified')
        await self.confirm(await self.delivery('/api/settings/relay/test'))
        self.assertTrue(self.relay.otp_ready())
        self.assertEqual((await self.rows())[second]['outbound_readiness'], 'unverified')

    async def confirmed_senders(self):
        email = 'sender@rsalehin24.me'
        await self.create(email)
        sender = await self.delivery('/api/accounts/' + email + '/test-sender')
        fixed = await self.delivery('/api/settings/relay/test')
        await self.confirm(sender)
        await self.confirm(fixed)
        self.assertTrue(self.relay.otp_ready())
        self.assertEqual((await self.rows())[email]['outbound_readiness'], 'confirmed')
        return sender, fixed

    async def assert_saved_revision_invalidates(self, settings):
        identifiers = await self.confirmed_senders()
        response = await self.client.post('/api/settings/relay', json=settings)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['otp_ready'])
        self.assertEqual((await self.rows())['sender@rsalehin24.me']['outbound_readiness'],
                         'unverified')
        for identifier in identifiers:
            result = await self.client.post('/api/relay-tests/' + identifier + '/confirm', json={})
            self.assertEqual(result.status_code, 409)

    async def test_saved_credential_revision_revokes_all_previous_sender_receipts(self):
        await self.assert_saved_revision_invalidates({
            **self.settings, 'credential': 'replacement-fixture-secret',
        })

    async def test_resaving_after_manual_sender_domain_changes_requires_fresh_receipts(self):
        # Establish the old receipt before the external provider's sender policy changes.
        identifiers = await self.confirmed_senders()
        self.peer.reject_sender = True
        response = await self.client.post('/api/settings/relay', json=self.settings)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.relay.otp_ready())
        self.assertEqual((await self.rows())['sender@rsalehin24.me']['outbound_readiness'],
                         'unverified')
        for identifier in identifiers:
            result = await self.client.post('/api/relay-tests/' + identifier + '/confirm', json={})
            self.assertEqual(result.status_code, 409)

    async def test_closed_restore_revokes_both_previously_confirmed_sender_receipts(self):
        await self.confirmed_senders()
        OfflineRecovery(self.fixture.auth).prepare_restore()
        self.assertEqual((await self.client.get('/api/settings/relay')).status_code, 503)
        self.assertFalse(self.relay.otp_ready())
        with self.fixture.database.transaction() as database:
            self.assertIsNone(database.execute('SELECT readiness_test FROM relay_settings')
                              .fetchone()[0])
            self.assertIsNone(database.execute('SELECT outbound_test FROM mailboxes').fetchone()[0])

    async def test_polling_sends_nothing_and_excess_explicit_sends_do_not_reach_smtp_or_logs(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        for _ in range(5):
            for route in ('/api/status', '/api/settings/relay', '/api/settings/relay/quota',
                          '/api/diagnostics', '/api/accounts'):
                self.assertEqual((await self.client.get(route)).status_code, 200)
        self.assertEqual(self.peer.messages, [])
        self.assertEqual(self.peer.events, [])
        for _ in range(3):
            await self.delivery('/api/settings/relay/test')
        before = list(self.peer.events)
        denied = await self.client.post('/api/settings/relay/test', json={'mode': 'delivery'})
        self.assertEqual(denied.status_code, 429)
        self.assertEqual(denied.headers['retry-after'], '900')
        self.assertEqual(self.peer.events, before)
        self.assertEqual(len(self.peer.messages), 3)
        with self.fixture.database.transaction() as database:
            audit = str([tuple(row) for row in database.execute('SELECT * FROM audit_logs')])
        for secret in (self.settings['credential'], MAILBOX_PASSWORD, self.fixture.seed):
            self.assertNotIn(secret, output.getvalue() + audit)
