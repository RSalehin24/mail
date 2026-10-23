import uuid

from tests.api_support import ApiTestCase
from tests.auth_support import PASSWORD
from tests.helper_support import MAILBOX_PASSWORD


class AuthorizationMatrixTests(ApiTestCase):
    def protected_requests(self):
        email = "fixture@rsalehin24.me"
        create = {"request_id": str(uuid.uuid4()), "email": email, "password": MAILBOX_PASSWORD}
        return (
            ("GET", "/api/accounts", None), ("GET", "/api/diagnostics", None),
            ("GET", "/api/settings/relay", None), ("GET", "/api/settings/relay/quota", None),
            ("POST", "/api/accounts/create", create),
            ("POST", f"/api/accounts/{email}/password",
             {"request_id": str(uuid.uuid4()), "password": MAILBOX_PASSWORD}),
            ("DELETE", f"/api/accounts/{email}", {"request_id": str(uuid.uuid4()),
             "confirmation": email, "cutoff": "untrusted", "acknowledged": True}),
            ("POST", "/api/settings/relay/reveal", {}),
            ("POST", "/api/settings/relay/test", {"mode": "delivery"}),
        )

    async def assert_protected_routes_denied(self):
        for method, path, body in self.protected_requests():
            response = await self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_invalid_sessions_deny_all_protected_routes(self):
        expired = self.fixture.login()
        self.fixture.now += 900
        revoked = self.fixture.login()
        self.fixture.auth.logout(revoked)
        states = {"anonymous": "", "password_only": self.fixture.challenge(),
                  "expired": expired, "revoked": revoked}
        for name, token in states.items():
            with self.subTest(state=name):
                self.set_session(token)
                await self.assert_protected_routes_denied()
        self.assertEqual(self.fixture.host.maddy.calls, [])
        self.assertEqual(self.fixture.transport.messages, [])

    async def test_invalid_host_origin_and_csrf_context_cannot_mutate_with_a_valid_cookie(self):
        self.set_session(self.fixture.login())
        payload = self.protected_requests()[4][2]
        for name, value, expected in (
            ("Host", "evil.example", 400), ("Host", "mail.rsalehin24.me.evil.example", 400),
            ("Origin", None, 403), ("Origin", "null", 403),
            ("Origin", "https://evil.example", 403),
            ("Content-Type", "application/x-www-form-urlencoded", 415),
        ):
            request = self.client.build_request("POST", "/api/accounts/create", json=payload)
            if value is None:
                request.headers.pop(name, None)
            else:
                request.headers[name] = value
            response = await self.client.send(request)
            self.assertEqual(response.status_code, expected)
        self.assertEqual(self.fixture.host.maddy.calls, [])
        self.assertEqual(self.fixture.transport.messages, [])

    def absolute_expiry(self, token):
        with self.fixture.database.transaction() as connection:
            return connection.execute("SELECT absolute_expiry FROM sessions WHERE token_hash=?",
                                      (self.fixture.keys.digest(token, "session"),)).fetchone()[0]

    def stored_password_hash(self):
        with self.fixture.database.transaction() as connection:
            return connection.execute("SELECT password_hash FROM admin_auth").fetchone()[0]

    async def test_master_password_bounds_and_exact_unicode_spaces_survive_real_hashing(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        before = self.stored_password_hash()
        for value in ("", "x" * 15, "x" * 1025, "é" * 512 + " "):
            response = await self.client.post("/api/security/password", json={"password": value})
            self.assertIn(response.status_code, {400, 422})
            self.assertEqual(self.stored_password_hash(), before)
        accepted = " " + "é" * 511 + " "
        self.assertEqual(len(accepted.encode()), 1024)
        response = await self.client.post("/api/security/password", json={"password": accepted})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 401)
        for value, expected in ((accepted, 200), (accepted.strip(), 401), (PASSWORD, 401)):
            response = await self.client.post("/api/login", json={
                "email": "rsalehin24@gmail.com", "password": value,
            })
            self.assertEqual(response.status_code, expected)

    async def test_reauthentication_binds_session_purpose_expiry_without_extending_session(self):
        original = self.fixture.login()
        before = self.absolute_expiry(original)
        self.set_session(original)
        response = await self.client.post("/api/security/reauth", json={
            "email": "rsalehin24@gmail.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        factor = {"challenge": response.json()["challenge"], "factor": "recovery",
                  "code": self.fixture.codes[0]}
        self.assertEqual((await self.client.post("/api/2fa/verify", json=factor)).status_code, 401)
        self.fixture.now += 30
        self.set_session(self.fixture.login())
        self.assertEqual((await self.client.post("/api/security/reauth/verify", json=factor))
                         .status_code, 401)
        self.set_session(original)
        response = await self.client.post("/api/security/reauth/verify", json=factor)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"grant_seconds": 300})
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(self.absolute_expiry(original), before)
        self.fixture.now += 300
        response = await self.client.post("/api/security/password", json={
            "password": " new password must not become committed ",
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "recent_authentication_required")
        self.assertEqual(self.absolute_expiry(original), before)
