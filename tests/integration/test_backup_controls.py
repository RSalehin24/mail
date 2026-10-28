import uuid

from tests.api_support import ApiTestCase
from tests.recovery_support import configure_recovery


class BackupApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        configure_recovery(self.fixture)

    async def test_authenticated_catalog_and_recent_authentication_on_every_mutation(self):
        body = {"request_id": str(uuid.uuid4()), "kind": "backup", "acknowledged": True}
        self.assertEqual((await self.client.get("/api/backups")).status_code, 401)
        self.assertEqual(
            (await self.client.post("/api/backups/operations", json=body)).status_code, 401
        )
        token = self.fixture.login()
        self.set_session(token)
        self.assertEqual((await self.client.get("/api/backups")).status_code, 200)
        self.assertEqual((await self.client.get("/api/backups?page_size=1000")).status_code, 400)
        self.assertEqual(
            (await self.client.post("/api/backups/operations", json=body)).status_code, 403
        )
        self.fixture.reauth(token)
        response = await self.client.post("/api/backups/operations", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "queued")
        repeat = await self.client.post("/api/backups/operations", json=body)
        self.assertEqual(repeat.json()["id"], response.json()["id"])
        self.assertNotIn("payload", response.json())
        self.assertEqual(
            (
                await self.client.post(
                    "/api/backups/operations", json={**body, "acknowledged": False}
                )
            ).status_code,
            409,
        )

    async def test_history_pagination_includes_older_relay_revisions(self):
        token = self.fixture.login()
        self.set_session(token)
        with self.fixture.services.relay.database.transaction() as connection:
            for i in range(125):
                connection.execute(
                    "INSERT INTO relay_tests VALUES(?,?,?,?,0,0,0,?)",
                    (
                        str(uuid.uuid4()),
                        i + 1,
                        "mail@rsalehin24.me",
                        "rsalehin24@gmail.com",
                        1000 + i,
                    ),
                )
        result = await self.client.get(
            "/api/relay-tests?sender=mail@rsalehin24.me&page=99&page_size=10"
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            (result.json()["page"], result.json()["total"], len(result.json()["items"])),
            (13, 125, 5),
        )
