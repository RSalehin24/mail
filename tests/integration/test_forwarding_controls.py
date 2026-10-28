from tests.api_support import ApiTestCase
from tests.forwarding_support import configure_forwarding


class ForwardingApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fixture.host.maddy.storage.add("mail@rsalehin24.me")
        self.fixture.host.maddy.credentials.add("mail@rsalehin24.me")
        configure_forwarding(self.fixture)

    async def test_rules_require_authentication_recent_auth_and_strict_fields(self):
        self.assertEqual((await self.client.get("/api/forwarding")).status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        listing = (await self.client.get("/api/forwarding")).json()
        value = {
            "email": "mail@rsalehin24.me",
            "destination": "recipient@example.com",
            "enabled": True,
            "revision": listing["revision"],
        }
        self.assertEqual((await self.client.put("/api/forwarding", json=value)).status_code, 403)
        self.fixture.reauth(token)
        response = await self.client.put("/api/forwarding", json=value)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["rules"][0]["keep_local"])
        self.assertEqual(
            (
                await self.client.put("/api/forwarding", json={**value, "enabled": "true"})
            ).status_code,
            422,
        )
        self.assertEqual(
            (
                await self.client.post(
                    "/api/forwarding/retry", json={"email": value["email"], "acknowledged": False}
                )
            ).status_code,
            400,
        )
        response = await self.client.post(
            "/api/forwarding/remove",
            json={"email": value["email"], "revision": response.json()["revision"]},
        )
        self.assertEqual(response.json()["rules"], [])


    async def test_verification_requires_recent_auth_and_enqueues_only_a_saved_destination(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        values = {'email': 'mail@rsalehin24.me', 'destination': 'recipient@example.com',
                  'enabled': True, 'revision': 0}
        self.assertEqual((await self.client.put('/api/forwarding', json=values)).status_code, 200)
        response = await self.client.post('/api/forwarding/verify', json={'email': values['email']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rules'][0]['verification_state'], 'queued')
        response = await self.client.post('/api/forwarding/verify', json={
            'email': values['email'], 'destination': 'unrelated@example.com'})
        self.assertEqual(response.status_code, 422)

    async def test_local_verification_requires_recent_auth_and_an_existing_mailbox(self):
        body = {'email': 'mail@rsalehin24.me'}
        response = await self.client.post('/api/forwarding/verify-local', json=body)
        self.assertEqual(response.status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        response = await self.client.post('/api/forwarding/verify-local', json=body)
        self.assertEqual(response.status_code, 403)
        self.fixture.reauth(token)
        response = await self.client.post('/api/forwarding/verify-local', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        with self.fixture.host.helper.dependencies.forwarding.connect() as db:
            row = db.execute('SELECT destination,state FROM verifications').fetchone()
        self.assertEqual(tuple(row), ('mail@delivery.rsalehin24.me', 'queued'))
        self.assertEqual((await self.client.post('/api/forwarding/verify-local', json={
            **body, 'destination': 'other@example.com'})).status_code, 422)
        self.assertEqual((await self.client.post('/api/forwarding/verify-local', json={
            'email': 'missing@rsalehin24.me'})).status_code, 404)
