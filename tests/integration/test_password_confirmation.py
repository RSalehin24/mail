from tests.api_support import ApiTestCase
from tests.auth_support import PASSWORD

LOGIN = {"email": "rsalehin24@gmail.com", "password": PASSWORD}
METADATA = {"display_name": "Example", "notes": ""}
OPERATION = ("PATCH", "/api/accounts/fixture@rsalehin24.me/metadata", METADATA)
SETTING = ("POST", "/api/security/password", {"password": "x" * 20})


class PasswordConfirmationTests(ApiTestCase):
    async def send(self, request):
        method, path, body = request
        return await self.client.request(method, path, json=body)

    async def test_operations_ask_for_the_password_and_settings_still_ask_for_the_code(self):
        self.set_session(self.fixture.login())
        response = await self.send(OPERATION)
        self.assertEqual((response.status_code, response.json()["error"]),
                         (403, "password_confirmation_required"))
        self.assertEqual(
            (await self.client.post("/api/security/confirm", json=LOGIN)).json(),
            {"grant_seconds": 300},
        )
        self.assertNotEqual((await self.send(OPERATION)).status_code, 403)
        response = await self.send(SETTING)
        self.assertEqual((response.status_code, response.json()["error"]),
                         (403, "recent_authentication_required"))

    async def test_wrong_password_grants_nothing(self):
        self.set_session(self.fixture.login())
        response = await self.client.post(
            "/api/security/confirm", json={**LOGIN, "password": "wrong password value"}
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual((await self.send(OPERATION)).status_code, 403)

    async def test_confirmation_expires_after_five_minutes(self):
        self.set_session(self.fixture.login())
        await self.client.post("/api/security/confirm", json=LOGIN)
        self.fixture.now += 301
        self.assertEqual((await self.send(OPERATION)).status_code, 403)

    async def test_confirmation_belongs_to_one_session(self):
        self.set_session(self.fixture.login())
        await self.client.post("/api/security/confirm", json=LOGIN)
        self.fixture.now += 30  # a new authenticator step; a used code cannot be replayed
        self.set_session(self.fixture.login())
        self.assertEqual((await self.send(OPERATION)).status_code, 403)

    async def test_full_reauthentication_also_unlocks_operations(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        self.assertNotEqual((await self.send(OPERATION)).status_code, 403)

    async def test_confirmation_needs_a_session(self):
        self.set_session("")
        self.assertEqual((await self.client.post("/api/security/confirm", json=LOGIN))
                         .status_code, 401)
