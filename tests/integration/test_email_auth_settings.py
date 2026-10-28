from tests.api_support import ApiTestCase


class EmailAuthenticationApiTests(ApiTestCase):
    async def test_settings_authentication_and_delivery_gate(self):
        path = "/api/security/email-authentication"
        self.assertEqual((await self.client.get(path)).status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        self.assertEqual((await self.client.get(path)).status_code, 200)
        self.assertEqual((await self.client.put(path, json={"enabled": True})).status_code, 403)
        self.fixture.reauth(token)
        response = await self.client.put(path, json={"enabled": True})
        self.assertEqual(response.status_code, 409)
        self.fixture.ready_relay()
        response = await self.client.put(path, json={"enabled": True})
        self.assertTrue(response.json()["available"])
        self.assertEqual((await self.client.put(path, json={"enabled": "true"})).status_code, 422)
        self.assertFalse((await self.client.put(path, json={"enabled": False})).json()["enabled"])
