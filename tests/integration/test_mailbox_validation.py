import dataclasses
import uuid

from backend.auth import Authentication, Verification
from tests.api_support import ApiTestCase
from tests.helper_support import MAILBOX_PASSWORD
from tests.protocol_support import INVALID_ADDRESSES, INVALID_PASSWORDS


class MailboxValidationApiTests(ApiTestCase):
    async def test_http_boundary_rejects_the_same_address_and_password_values_before_any_cli(self):
        self.set_session(self.fixture.login())
        base = {'request_id': str(uuid.uuid4()), 'email': 'valid@rsalehin24.me',
                'password': MAILBOX_PASSWORD}
        payloads = [{**base, 'email': value} for value in INVALID_ADDRESSES]
        payloads += [{**base, 'password': value} for value in INVALID_PASSWORDS]
        payloads += [{**base, 'command': 'id'}, {**base, 'container': 'maddy'},
                     {**base, 'path': '/etc/shadow'}]
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                response = await self.client.post('/api/accounts/create', json=payload)
                self.assertIn(response.status_code, {400, 422})
                self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.fixture.host.maddy.calls, [])
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)

    async def test_disabled_capabilities_refuse_deletion_and_email_even_with_confirmed_relay(self):
        self.fixture.ready_relay()
        challenge = self.fixture.challenge()
        values = []
        self.fixture.auth.send_otp(Verification(challenge, 'email', '', 'fixture-send'),
                                   values.append)
        self.fixture.services.authentication = Authentication(dataclasses.replace(
            self.fixture.auth.dependencies, email_enabled=False,
        ))
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        email = 'disabled@rsalehin24.me'
        payload = {'request_id': str(uuid.uuid4()), 'confirmation': email,
                   'cutoff': 'untrusted', 'acknowledged': True}
        response = await self.client.request('DELETE', '/api/accounts/' + email, json=payload)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'permanent_deletion_disabled')
        payload['permanent_deletion_enabled'] = True
        response = await self.client.request('DELETE', '/api/accounts/' + email, json=payload)
        self.assertEqual(response.status_code, 422)
        response = await self.client.post('/api/2fa/send-otp',
                                          json={'challenge': challenge, 'purpose': 'login'})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post('/api/2fa/verify', json={
            'challenge': challenge, 'factor': 'email', 'code': values[0],
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'email_otp_unavailable')
        self.assertEqual(self.fixture.host.maddy.calls, [])
        self.assertEqual(self.fixture.transport.messages, [])

    async def test_mailbox_details_require_recent_auth_and_persist_without_password_changes(self):
        email = 'details@rsalehin24.me'
        token = self.fixture.login()
        self.set_session(token)
        await self.client.post('/api/accounts/create', json={
            'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD,
        })
        calls = list(self.fixture.host.maddy.calls)
        payload = {'display_name': 'Operations', 'notes': 'Account owner'}
        response = await self.client.patch('/api/accounts/'+email+'/metadata', json=payload)
        self.assertEqual(response.status_code, 403)
        self.fixture.reauth(token)
        response = await self.client.patch('/api/accounts/'+email+'/metadata', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['display_name'], 'Operations')
        accounts = (await self.client.get('/api/accounts')).json()
        self.assertEqual(accounts[0]['display_name'], 'Operations')
        self.assertEqual(self.fixture.host.maddy.calls, calls)

    async def test_detail_route_is_authenticated_and_returns_live_identity_and_metadata(self):
        email = 'details@rsalehin24.me'
        response = await self.client.get('/api/accounts/' + email)
        self.assertEqual(response.status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        response = await self.client.get('/api/accounts/' + email)
        self.assertEqual(response.status_code, 404)
        await self.client.post('/api/accounts/create', json={
            'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD,
        })
        response = await self.client.get('/api/accounts/' + email)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['email'], email)
        self.assertTrue(response.json()['credentials_present'])
        self.assertNotIn('password', response.json())
        self.assertEqual(response.json()['sender_tests'], [])

    async def test_icon_files_are_served_with_their_mime_type_and_missing_assets_are_404(self):
        static = self.fixture.services.settings.static_directory
        (static / 'favicon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        response = await self.client.get('/favicon.svg')
        self.assertEqual(response.status_code, 200)
        self.assertIn('image/svg+xml', response.headers['content-type'])
        self.assertTrue(response.text.startswith('<svg'))
        for path in ['/icon.svg', '/missing.png', '/manifest.webmanifest']:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 404)
        response = await self.client.get('/mailboxes/details%40rsalehin24.me/configuration')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/html', response.headers['content-type'])
