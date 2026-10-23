import asyncio
from unittest.mock import patch

import pyotp

from tests.api_support import ApiTestCase, FreshApiFixture
from tests.auth_support import PASSWORD
from tests.totp_api_support import TotpLogin


class SetupApiTests(ApiTestCase):
    fixture_factory = FreshApiFixture

    async def test_setup_totp_step_cannot_be_reused_to_issue_a_login_session(self):
        with patch('backend.enrollment.pyotp.random_base32',
                   return_value='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'):
            token, secret = await self.begin()
        payload = self.confirmation(token, secret)
        self.assertEqual((await self.client.post('/api/setup', json=payload)).status_code, 200)
        login = TotpLogin(self, self.client)
        await login.rejected(payload['code'])
        self.fixture.now += 30
        fresh = pyotp.TOTP(secret).at(self.fixture.now)
        self.assertEqual((await login.verify(fresh)).status_code, 200)
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)
        await login.rejected(fresh)
        with self.fixture.database.transaction() as connection:
            watermark = connection.execute('SELECT last_step FROM admin_auth').fetchone()[0]
        self.assertEqual(watermark, self.fixture.now // 30)

    async def begin(self):
        token = self.fixture.enrollment.issue_bootstrap()
        result = await self.client.post("/api/setup", json={"token": token})
        self.assertEqual(result.status_code, 200)
        return token, result.json()["secret"]

    def confirmation(self, token, secret):
        return {"token": token, "password": PASSWORD,
                "code": pyotp.TOTP(secret).at(self.fixture.now)}

    async def test_missing_wrong_expired_and_reused_tokens_are_rejected(self):
        for payload, status in (({}, 422), ({"token": "wrong"}, 403), ({"token": ""}, 403)):
            result = await self.client.post("/api/setup", json=payload)
            self.assertEqual(result.status_code, status)
        expired = self.fixture.enrollment.issue_bootstrap()
        self.fixture.now += 1800
        result = await self.client.post("/api/setup", json={"token": expired})
        self.assertEqual(result.status_code, 403)
        token, secret = await self.begin()
        payload = self.confirmation(token, secret)
        self.assertEqual((await self.client.post("/api/setup", json=payload)).status_code, 200)
        self.assertEqual((await self.client.post("/api/setup", json=payload)).status_code, 403)
        self.assertEqual((await self.client.post("/api/setup", json={"token": token})).status_code,
                         403)

    async def test_wrong_six_digit_totp_does_not_initialize_or_issue_recovery_codes(self):
        token, secret = await self.begin()
        valid = {pyotp.TOTP(secret).at(self.fixture.now + offset) for offset in (-30, 0, 30)}
        wrong = next(f"{number:06d}" for number in range(4) if f"{number:06d}" not in valid)
        payload = {**self.confirmation(token, secret), "code": wrong}
        result = await self.client.post("/api/setup", json=payload)
        self.assertEqual(result.status_code, 401)
        self.assertNotIn("recovery_codes", result.json())
        with self.fixture.database.transaction() as connection:
            initialized, password = connection.execute(
                "SELECT initialized,password_hash FROM admin_auth"
            ).fetchone()
            self.assertEqual(initialized, 0)
            self.assertEqual(password, "")
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0], 0
            )

    async def test_competing_setup_requests_create_exactly_one_initialized_admin(self):
        token, secret = await self.begin()
        payload = self.confirmation(token, secret)
        results = await asyncio.gather(*(
            self.client.post("/api/setup", json=payload) for _ in range(2)
        ))
        self.assertEqual(sum(result.status_code == 200 for result in results), 1)
        self.assertTrue(all(result.status_code in {200, 403, 503} for result in results))
        self.assertEqual((await self.client.post("/api/setup", json=payload)).status_code, 403)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM admin_auth WHERE initialized=1"
            ).fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0], 5
            )
