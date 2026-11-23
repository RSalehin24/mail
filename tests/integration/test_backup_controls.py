import uuid

from tests.api_support import ApiTestCase
from tests.recovery_support import configure_recovery


class BackupApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        configure_recovery(self.fixture)

    async def test_catalog_is_read_only_and_needs_a_session(self):
        self.assertEqual((await self.client.get("/api/backups")).status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        self.assertEqual((await self.client.get("/api/backups")).status_code, 200)
        self.assertEqual((await self.client.get("/api/backups?kind=operations")).status_code, 200)
        self.assertEqual((await self.client.get("/api/backups?page_size=1000")).status_code, 400)

    async def test_backing_up_and_restoring_are_not_dashboard_operations(self):
        token = self.fixture.login()
        self.set_session(token)
        self.fixture.reauth(token)
        body = {"request_id": str(uuid.uuid4()), "kind": "backup", "acknowledged": True}
        for method, path in (
            ("post", "/api/backups/operations"),
            ("get", f"/api/backups/operations/{uuid.uuid4()}"),
        ):
            options = {"json": body} if method == "post" else {}
            response = await getattr(self.client, method)(path, **options)
            self.assertIn(response.status_code, {404, 405}, path)

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
