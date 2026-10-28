import uuid
from unittest.mock import Mock

from backend.relay import Relay, RelayDependencies
from shared.errors import ServiceError
from tests.api_support import ApiTestCase
from tests.fixture_values import MAILBOX_PASSWORD


class SubmissionApiTests(ApiTestCase):
    async def test_client_configuration_uses_only_mailbox_credentials(self):
        self.set_session(self.fixture.login())
        email = 'submission@rsalehin24.me'
        response = await self.client.post('/api/accounts/create', json={
            'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD})
        self.assertEqual(response.status_code, 200)
        response = await self.client.get('/api/accounts/' + email + '/config')
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(value['incoming']['port'], 993)
        self.assertEqual(value['outgoing'], {
            'host': 'mail.rsalehin24.me', 'port': 587, 'security': 'STARTTLS',
            'username': email, 'password': 'Use the same mailbox password as IMAP.'})
        self.assertNotIn('brevo', response.text.lower())

    async def test_sync_failure_returns_error_and_preserves_saved_provider_revision(self):
        callback = Mock()
        relay = Relay(RelayDependencies(self.fixture.database, self.fixture.keys,
                      self.fixture.transport, lambda: self.fixture.now, callback))
        self.fixture.services.relay = relay
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        values = {'host': 'smtp-relay.brevo.com', 'port': 587, 'tls_mode': 'starttls',
                  'username': 'synthetic', 'credential': 'fixture-secret'}
        response = await self.client.post('/api/settings/relay', json=values)
        self.assertEqual(response.status_code, 200)
        callback.assert_called_once_with({'username': 'synthetic', 'credential': 'fixture-secret',
                                          'revision': 1})
        before = relay._configuration()
        callback.side_effect = ServiceError('submission_synchronization_failed', 503)
        response = await self.client.post('/api/settings/relay', json={
            **values, 'credential': 'replacement-secret'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('submission_synchronization_failed', response.text)
        self.assertNotIn('replacement-secret', response.text)
        self.assertEqual(relay._configuration(), before)
