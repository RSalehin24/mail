import asyncio
import threading
from unittest.mock import patch

from backend.authorization import COOKIE_NAME
from backend.work import WorkPool
from shared.errors import ServiceError
from tests.api_support import ApiTestCase
from tests.auth_support import PASSWORD


class ApiTests(ApiTestCase):
    def login_direct(self):
        token = self.fixture.login()
        self.client.cookies.set(COOKIE_NAME, token, domain="mail.rsalehin24.me", path="/")
        return token

    async def test_anonymous_and_password_only_routes_are_denied(self):
        for path in ("/api/accounts", "/api/diagnostics", "/api/settings/relay"):
            response = await self.client.get(path)
            self.assertIn(response.status_code, (401, 404))
            self.assertEqual(response.headers["cache-control"], "no-store")
        response = await self.client.post(
            "/api/login", json={"email": "rsalehin24@gmail.com", "password": PASSWORD}
        )
        self.assertEqual(response.status_code, 200)
        self.client.cookies.set(COOKIE_NAME, response.json()["challenge"])
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 401)
        self.assertEqual(self.fixture.host.maddy.calls, [])

    async def test_full_login_cookie_flags_and_logout(self):
        challenge = (
            await self.client.post(
                "/api/login", json={"email": "rsalehin24@gmail.com", "password": PASSWORD}
            )
        ).json()["challenge"]
        response = await self.client.post(
            "/api/2fa/verify",
            json={"challenge": challenge, "factor": "totp", "code": self.fixture.code()},
        )
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Path=/"):
            self.assertIn(attribute, cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 200)
        self.assertEqual((await self.client.post("/api/logout", json={})).status_code, 200)
        self.assertEqual((await self.client.get("/api/accounts")).status_code, 401)

    async def test_host_origin_and_validation_errors_have_no_side_effects_or_secrets(self):
        self.login_direct()
        hostile = await self.client.get("/api/accounts", headers={"host": "evil.example"})
        self.assertEqual(hostile.status_code, 400)
        response = await self.client.post(
            "/api/accounts/create",
            headers={"origin": "https://evil.example"},
            json={"password": "sensitive fixture"},
        )
        self.assertEqual(response.status_code, 403)
        invalid = await self.client.post(
            "/api/accounts/create", json={"password": "sensitive fixture"}
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertNotIn("sensitive fixture", invalid.text)
        self.assertEqual(invalid.headers["cache-control"], "no-store")
        self.assertEqual(self.fixture.host.maddy.calls, [])

    async def test_streamed_body_without_length_is_bounded_before_routing(self):
        async def chunks():
            for _ in range(7):
                yield b"x" * 10000

        response = await self.client.post(
            "/api/login", content=chunks(), headers={"content-type": "application/json"}
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.fixture.host.maddy.calls, [])

    async def test_exact_body_limit_and_misleading_lengths_cannot_bypass_stream_counting(self):
        payload = b'{"email":"rsalehin24@gmail.com","password":" wrong fixture password "}'
        for declared in (None, "1", "999999"):
            for size, status in ((65536, 401), (65537, 413)):
                await self.assert_body_boundary((payload, size), (declared, status))

    async def assert_body_boundary(self, body, expectation):
        payload, size = body
        declared, status = expectation

        async def chunks():
            content = payload + b" " * (size - len(payload))
            for offset in range(0, len(content), 8192):
                yield content[offset:offset + 8192]

        headers = {"content-type": "application/json"}
        if declared is not None:
            headers["content-length"] = declared
        with patch.object(self.fixture.auth, "password_challenge",
                          wraps=self.fixture.auth.password_challenge) as password_check:
            response = await self.client.post("/api/login", content=chunks(), headers=headers)
        self.assertEqual(response.status_code, status)
        self.assertEqual(password_check.call_count, int(size <= 65536))
        self.assertEqual(self.fixture.host.maddy.calls, [])

    async def test_unknown_api_never_falls_back_to_frontend_html(self):
        response = await self.client.get("/api/unknown")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "api_not_found")
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_unexpected_error_is_safe_json_with_no_store(self):
        self.login_direct()
        with patch.object(
            self.fixture.services.mailboxes, "inventory", side_effect=RuntimeError("secret fixture")
        ):
            with self.assertLogs("backend.boundary", level="ERROR") as captured:
                response = await self.client.get("/api/accounts")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"], "internal_error")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("secret fixture", response.text + "".join(captured.output))
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    async def test_cancelled_work_keeps_capacity_until_actual_completion(self):
        pool = WorkPool(1, 5)
        started, release = threading.Event(), threading.Event()

        def work():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")

        task = asyncio.create_task(pool.run(work))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(ServiceError):
            await pool.run(lambda: None)
        release.set()
        await asyncio.to_thread(lambda: pool.executor.submit(lambda: None).result(2))
        self.assertEqual(await pool.run(lambda: "ready"), "ready")
        pool.close()

    async def test_cancelled_hash_retains_its_permit_until_real_hashing_finishes(self):
        pool = WorkPool(1, 5)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(pool.close)
        self.addCleanup(release.set)
        encoded = []

        def hash_password():
            started.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            encoded.append(self.fixture.hasher.hash(PASSWORD))

        task = asyncio.create_task(pool.run(hash_password))
        self.assertTrue(await asyncio.to_thread(started.wait, 2))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(ServiceError) as caught:
            await pool.run(lambda: "must not run")
        self.assertEqual(caught.exception.code, "work_overloaded")
        self.assertEqual(encoded, [])
        release.set()
        await asyncio.to_thread(lambda: pool.executor.submit(lambda: None).result(2))
        self.assertTrue(self.fixture.hasher.verify(encoded[0], PASSWORD))
        self.assertEqual(await pool.run(lambda: "ready"), "ready")
