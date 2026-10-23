from unittest.mock import patch

import pyotp

from backend.auth import Credentials, Verification
from backend.security import Security
from shared.errors import ServiceError
from tests.api_support import ApiTestCase
from tests.auth_support import PASSWORD
from tests.totp_api_support import TotpLogin


class SecurityApiTests(ApiTestCase):
    async def test_replacement_preserves_and_advances_the_totp_replay_watermark(self):
        self.set_session(self.fixture.login())
        await self.grant()
        old_seed, watermark = self.admin()['totp_secret'], self.admin()['last_step']
        with patch('backend.security.pyotp.random_base32',
                   return_value='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'):
            material = await self.client.post('/api/security/totp', json={})
        self.assertEqual(material.status_code, 200)
        secret = material.json()['secret']
        rejected = await self.client.post('/api/security/totp', json={
            'code': pyotp.TOTP(secret).at(self.fixture.now),
        })
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(self.admin()['totp_secret'], old_seed)
        self.assertEqual(self.admin()['last_step'], watermark)
        self.fixture.now += 30
        code = pyotp.TOTP(secret).at(self.fixture.now)
        accepted = await self.client.post('/api/security/totp', json={'code': code})
        self.assertEqual(accepted.status_code, 200)
        login = TotpLogin(self, self.client)
        await login.rejected(code)
        self.fixture.now += 30
        fresh = pyotp.TOTP(secret).at(self.fixture.now)
        self.assertEqual((await login.verify(fresh)).status_code, 200)
        await login.rejected(fresh)
        self.assertEqual(self.admin()['last_step'], self.fixture.now // 30)

    async def grant(self):
        response = await self.client.post("/api/security/reauth", json={
            "email": "rsalehin24@gmail.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        response = await self.client.post("/api/security/reauth/verify", json={
            "challenge": response.json()["challenge"], "factor": "recovery",
            "code": self.fixture.codes.pop(),
        })
        self.assertEqual(response.status_code, 200)

    def admin(self):
        with self.fixture.database.transaction() as connection:
            return dict(connection.execute("SELECT * FROM admin_auth WHERE id=1").fetchone())

    def authentication_records(self):
        queries = (
            ("sessions", "SELECT * FROM sessions"),
            ("auth_challenges", "SELECT * FROM auth_challenges"),
            ("otp_codes", "SELECT * FROM otp_codes"),
            ("recovery_codes", "SELECT * FROM recovery_codes"),
        )
        with self.fixture.database.transaction() as connection:
            return {table: [tuple(row) for row in connection.execute(query)]
                    for table, query in queries}

    def prepare_pending_state(self, session):
        self.fixture.ready_relay()
        challenge = self.fixture.challenge()
        self.fixture.auth.send_otp(Verification(challenge, "email", "", "pending"), lambda _: None)
        self.fixture.auth.password_challenge(
            Credentials("rsalehin24@gmail.com", PASSWORD, "pending"), session,
        )
        Security(self.fixture.auth).begin_totp(session)
        return challenge

    def assert_invalidated(self):
        records = self.authentication_records()
        for table in ("sessions", "auth_challenges", "otp_codes"):
            self.assertEqual(records[table], [], table)
        self.assertIsNone(self.admin()["pending_secret"])
        self.assertIsNone(self.admin()["pending_session"])

    async def test_replacement_requires_password_and_an_existing_factor(self):
        self.set_session(self.fixture.login())
        self.assertEqual((await self.client.post("/api/security/totp", json={}))
                         .status_code, 403)
        rejected = await self.client.post("/api/security/reauth", json={
            "email": "rsalehin24@gmail.com", "password": " wrong password for replacement ",
        })
        self.assertEqual(rejected.status_code, 401)
        password_only = await self.client.post("/api/security/reauth", json={
            "email": "rsalehin24@gmail.com", "password": PASSWORD,
        })
        self.assertEqual(password_only.status_code, 200)
        self.assertEqual((await self.client.post("/api/security/totp", json={}))
                         .status_code, 403)
        self.assertIsNone(self.admin()["pending_secret"])
        await self.grant()
        response = await self.client.post("/api/security/totp", json={})
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.admin()["pending_secret"], response.json()["secret"])

    async def test_invalid_and_expired_replacement_preserve_the_active_factor(self):
        session = self.fixture.login()
        self.set_session(session)
        await self.grant()
        active = self.admin()["totp_secret"]
        response = await self.client.post("/api/security/totp", json={})
        secret = response.json()["secret"]
        valid = {pyotp.TOTP(secret).at(self.fixture.now + offset) for offset in (-30, 0, 30)}
        wrong = next(f"{value:06d}" for value in range(4) if f"{value:06d}" not in valid)
        response = await self.client.post("/api/security/totp", json={"code": wrong})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.admin()["totp_secret"], active)
        self.fixture.now += 600
        await self.grant()
        response = await self.client.post("/api/security/totp", json={
            "code": pyotp.TOTP(secret).at(self.fixture.now),
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "replacement_expired")
        self.assertEqual(self.admin()["totp_secret"], active)
        self.fixture.auth.require_session(self.fixture.login())

    async def test_replacement_revokes_old_authentication_and_recovery_material(self):
        old_session = self.fixture.login()
        self.fixture.now += 30
        current = self.fixture.login()
        self.set_session(current)
        await self.grant()
        challenge = self.prepare_pending_state(current)
        old_codes = set(row[0] for row in self.authentication_records()["recovery_codes"])
        response = await self.client.post("/api/security/totp", json={})
        secret = response.json()["secret"]
        self.fixture.now += 30
        response = await self.client.post("/api/security/totp", json={
            "code": pyotp.TOTP(secret).at(self.fixture.now),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["recovery_codes"]), 5)
        self.assert_invalidated()
        self.assertEqual(self.fixture.keys.open(self.admin()["totp_secret"], "totp"), secret)
        new_codes = set(row[0] for row in self.authentication_records()["recovery_codes"])
        self.assertTrue(old_codes.isdisjoint(new_codes))
        for token in (old_session, current):
            with self.assertRaises(ServiceError):
                self.fixture.auth.require_session(token)
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(challenge, "recovery", self.fixture.codes[0], "old"), "login",
            )

    async def test_password_change_invalidates_all_pending_state_and_allows_fresh_login(self):
        session = self.fixture.login()
        self.set_session(session)
        await self.grant()
        challenge = self.prepare_pending_state(session)
        before, records = self.admin(), self.authentication_records()
        password = " replacement password with exact spaces "
        response = await self.client.post("/api/security/password", json={"password": password})
        self.assertEqual(response.status_code, 200)
        self.assert_invalidated()
        after = self.admin()
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual((after["totp_secret"], after["last_step"]),
                         (before["totp_secret"], before["last_step"]))
        self.assertEqual(self.authentication_records()["recovery_codes"], records["recovery_codes"])
        with self.assertRaises(ServiceError):
            self.fixture.auth.verify(
                Verification(challenge, "recovery", self.fixture.codes[0], "old"), "login",
            )
        response = await self.client.post("/api/login", json={
            "email": "rsalehin24@gmail.com", "password": password,
        })
        self.assertEqual(response.status_code, 200)
        self.fixture.now += 30
        response = await self.client.post("/api/2fa/verify", json={
            "challenge": response.json()["challenge"], "factor": "totp",
            "code": self.fixture.code(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 200)

    async def test_failed_password_write_rolls_back_authentication_invalidation(self):
        session = self.fixture.login()
        self.set_session(session)
        await self.grant()
        self.prepare_pending_state(session)
        before, records = self.admin(), self.authentication_records()
        with self.fixture.database.transaction() as connection:
            connection.execute("CREATE TRIGGER reject_password BEFORE UPDATE OF password_hash "
                               "ON admin_auth BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        response = await self.client.post("/api/security/password", json={
            "password": " this password must not be committed ",
        })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], "storage_unavailable")
        self.assertEqual(self.admin(), before)
        self.assertEqual(self.authentication_records(), records)
        self.fixture.auth.require_session(session)
