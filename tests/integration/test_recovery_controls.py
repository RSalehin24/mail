import uuid

from shared.files import atomic_json
from tests.api_support import ApiTestCase
from tests.helper_support import MAILBOX_PASSWORD
from tests.recovery_support import configure_recovery


class RecoveryControlApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.control = configure_recovery(self.fixture)
        self.email = "cloud@rsalehin24.me"
        self.fixture.services.mailboxes.create(
            {
                "request_id": str(uuid.uuid4()),
                "email": self.email,
                "password": MAILBOX_PASSWORD,
                "display_name": "Cloud",
            }
        )

    def deletion(self, **changes):
        return {
            "request_id": str(uuid.uuid4()),
            "email": self.email,
            "confirmation": self.email,
            "acknowledged": True,
            "retention": "7_days",
            "policy_revision": 1,
            "permanent": False,
            **changes,
        }

    async def test_session_and_recent_authentication_protect_every_recovery_mutation(self):
        identifier = str(uuid.uuid4())
        paths = [
            ("/api/recovery/delete", self.deletion()),
            (
                f"/api/recovery/mailboxes/{identifier}/restore",
                {
                    "request_id": str(uuid.uuid4()),
                    "confirmation": self.email,
                    "acknowledged": True,
                    "password": MAILBOX_PASSWORD,
                },
            ),
            (
                f"/api/recovery/mailboxes/{identifier}/remove",
                {"request_id": str(uuid.uuid4()), "confirmation": self.email, "acknowledged": True},
            ),
            (
                f"/api/recovery/mailboxes/{identifier}/reconcile",
                {"request_id": str(uuid.uuid4()), "confirmation": self.email, "acknowledged": True},
            ),
        ]
        for path, body in paths:
            self.assertEqual((await self.client.post(path, json=body)).status_code, 401)
        self.assertEqual((await self.client.get("/api/recovery/policy")).status_code, 401)
        token = self.fixture.login()
        self.set_session(token)
        for path, body in paths:
            self.assertEqual((await self.client.post(path, json=body)).status_code, 403)
        self.assertEqual((await self.client.get("/api/recovery/policy")).status_code, 200)
        self.fixture.reauth(token)
        body = self.deletion()
        result = await self.client.post("/api/recovery/delete", json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["state"], "queued")
        self.assertEqual(
            (await self.client.post("/api/recovery/delete", json=body)).json(), result.json()
        )
        with self.control.records.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM recovery_jobs").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM recovery_audit").fetchone()[0], 1
            )

    async def test_root_policy_rejects_protected_permanent_and_stale_settings(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        for changes, expected in (
            ({"permanent": True}, 403),
            ({"policy_revision": 999}, 409),
            ({"confirmation": "wrong@rsalehin24.me"}, 400),
            ({"acknowledged": False}, 400),
            ({"retention": "365_days"}, 422),
        ):
            self.assertEqual(
                (
                    await self.client.post("/api/recovery/delete", json=self.deletion(**changes))
                ).status_code,
                expected,
            )
        self.fixture.services.mailboxes.create(
            {
                "request_id": str(uuid.uuid4()),
                "email": "mail@rsalehin24.me",
                "password": MAILBOX_PASSWORD,
            }
        )
        result = await self.client.post(
            "/api/recovery/delete",
            json=self.deletion(email="mail@rsalehin24.me", confirmation="mail@rsalehin24.me"),
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["error"], "protected_recipient")
        policy = self.control.policy()
        updated = {**policy, "protected": {}}
        self.assertEqual(
            (
                await self.client.put(
                    "/api/recovery/policy", json={"policy": updated, "acknowledged": False}
                )
            ).status_code,
            400,
        )
        result = await self.client.put(
            "/api/recovery/policy", json={"policy": updated, "acknowledged": True}
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["policy"]["revision"], 2)
        self.assertEqual(
            (
                await self.client.post(
                    "/api/recovery/delete",
                    json=self.deletion(
                        email="mail@rsalehin24.me",
                        confirmation="mail@rsalehin24.me",
                        policy_revision=2,
                    ),
                )
            ).status_code,
            200,
        )

    async def test_drifted_qualification_cannot_enable_or_execute_deletion(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        policy = self.control.policy()
        atomic_json(self.control.policy_path, {**policy, "enabled": False})
        atomic_json(
            self.control.directory / "recovery-qualified.json",
            {
                "schema_version": 1,
                "qualified": False,
                "files": {},
            },
        )
        result = await self.client.put(
            "/api/recovery/policy",
            json={
                "policy": policy,
                "acknowledged": True,
            },
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["error"], "recovery_not_qualified")
        self.assertFalse(self.control.policy()["enabled"])

    async def test_duplicate_delete_cannot_change_address_or_retention(self):
        token = self.fixture.login()
        self.fixture.reauth(token)
        self.set_session(token)
        payload = self.deletion()
        self.assertEqual(
            (await self.client.post("/api/recovery/delete", json=payload)).status_code, 200
        )
        for change in ({"retention": "forever"}, {"confirmation": "wrong@rsalehin24.me"}):
            response = await self.client.post("/api/recovery/delete", json={**payload, **change})
            self.assertEqual(response.status_code, 409)

    async def test_dont_retain_maps_to_permanent_and_cannot_bypass_root_policy(self):
        token = self.fixture.login()
        self.set_session(token)
        self.fixture.reauth(token)
        denied = await self.client.post(
            "/api/recovery/delete", json=self.deletion(retention="none")
        )
        self.assertEqual(denied.status_code, 403)
        policy = self.control.policy()
        policy["permanent_deletion_enabled"] = True
        atomic_json(self.control.policy_path, policy)
        body = self.deletion(retention="none")
        accepted = await self.client.post("/api/recovery/delete", json=body)
        self.assertEqual(accepted.status_code, 200)
        job = self.control.records.original_job(body["request_id"])
        self.assertTrue(job["payload"]["permanent"])
        repeated = await self.client.post("/api/recovery/delete", json=body)
        self.assertEqual(repeated.status_code, 200)

    async def test_numbered_deleted_mailbox_pages_include_all_records_and_clamp(self):
        token = self.fixture.login()
        self.set_session(token)
        from helper.retention import RecoveryPolicy

        policy = RecoveryPolicy.parse(self.control.policy())
        for i in range(125):
            self.control.records.begin(
                str(uuid.uuid4()), f"fixture{i}@rsalehin24.me", policy, "7_days", 1000 + i, "", ""
            )
            with self.control.records.transaction() as connection:
                connection.execute("UPDATE recovery_records SET state='deleted'")
        result = await self.client.get("/api/recovery/mailboxes?page=99&page_size=10")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(
            (result.json()["page"], result.json()["total"], len(result.json()["mailboxes"])),
            (13, 125, 5),
        )
