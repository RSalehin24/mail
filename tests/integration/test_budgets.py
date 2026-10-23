from unittest.mock import patch

import httpx

from backend.auth import Authentication
from backend.main import create_app
from tests.api_support import ApiTestCase
from tests.auth_support import PASSWORD


class AuthenticationBudgetApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.fixture.now += 901

    def peer_client(self, peer):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(self.fixture.services), client=(peer, 1234)
            ),
            base_url="https://mail.rsalehin24.me",
            headers={"origin": "https://mail.rsalehin24.me"},
        )
        self.addAsyncCleanup(client.aclose)
        return client

    async def login(self, client):
        return await client.post("/api/login", json={
            "email": "rsalehin24@gmail.com", "password": PASSWORD,
        })

    def assert_limited(self, response, retry):
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "rate_limited")
        self.assertEqual(response.headers["retry-after"], str(retry))
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_peer_budget_shares_routes_ignores_forwarded_addresses_and_survives_restart(self):
        client = self.peer_client("192.0.2.1")
        first = await self.login(client)
        self.assertEqual(first.status_code, 200)
        verified = await client.post("/api/2fa/verify", json={
            "challenge": first.json()["challenge"], "factor": "recovery",
            "code": self.fixture.codes[0],
        })
        self.assertEqual(verified.status_code, 200)
        routes = (("/api/login", {"email": "other@example.net", "password": PASSWORD}, 401),
                  ("/api/setup", {"token": "new-but-invalid"}, 403),
                  ("/api/2fa/verify", {"challenge": "new", "factor": "totp", "code": "123456"},
                   401),
                  ("/api/security/reauth", {"email": "rsalehin24@gmail.com",
                                            "password": PASSWORD}, 200))
        for index in range(13):
            path, payload, status = routes[index % len(routes)]
            response = await client.post(path, json=payload, headers={
                "x-forwarded-for": "198.51.100." + str(index + 1),
                "forwarded": "for=203.0.113." + str(index + 1),
            })
            self.assertEqual(response.status_code, status)
        self.fixture.services.authentication = Authentication(self.fixture.auth.dependencies)
        restarted = self.fixture.services.authentication
        with patch.object(restarted, "_check_password", wraps=restarted._check_password) as hashing:
            self.assert_limited(await self.login(client), 900)
            self.fixture.now += 899.999
            self.assert_limited(await self.login(client), 1)
        hashing.assert_not_called()
        self.fixture.now += 0.001
        self.assertEqual((await self.login(client)).status_code, 200)

    async def test_thirty_admin_attempts_share_real_peers_and_new_addresses_cannot_reset_them(self):
        for suffix in range(3):
            client = self.peer_client("192.0.2." + str(suffix + 10))
            for index in range(10):
                response = await client.post("/api/login", json={
                    "email": f"unknown-{suffix}-{index}@example.net", "password": PASSWORD,
                })
                self.assertEqual(response.status_code, 401)
        fresh = self.peer_client("203.0.113.1")
        self.fixture.services.authentication = Authentication(self.fixture.auth.dependencies)
        self.assert_limited(await self.login(fresh), 900)
        with self.fixture.database.transaction() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM auth_rate_limits WHERE bucket='auth' AND reserved_at>?",
                (self.fixture.now - 900,),
            ).fetchone()[0]
        self.assertEqual(count, 30)
        self.fixture.now += 900
        self.assertEqual((await self.login(fresh)).status_code, 200)
