import asyncio
import dataclasses
import json
import threading
import uuid
from unittest.mock import patch

import pyotp

from backend.webmail_services import MailServices
from shared.errors import ServiceError
from tests.api_support import ApiTestCase
from tests.fixture_values import MAILBOX_PASSWORD
from tests.webmail_support import WebmailApiFixture


class WebmailSecurityTests(ApiTestCase):
    fixture_factory = WebmailApiFixture

    async def login_mailbox(self, email="first@rsalehin24.me"):
        response = await self.client.post(
            "/api/login", json={"email": email, "password": MAILBOX_PASSWORD}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.cookies.get("__Host-mail_admin")

    async def test_role_separation_and_session_identity(self):
        token = await self.login_mailbox()
        result = (await self.client.get("/api/session")).json()
        self.assertEqual(result["role"], "mailbox")
        self.assertEqual(result["user"]["email"], "first@rsalehin24.me")
        for path in (
            "/api/accounts",
            "/api/diagnostics",
            "/api/settings/relay",
            "/api/security/email-authentication",
            "/api/telemetry",
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 403, path)
        self.set_session(self.fixture.login())
        self.assertEqual((await self.client.get("/api/profile")).status_code, 403)
        self.assertEqual((await self.client.get("/api/webmail/labels")).status_code, 403)
        self.assertEqual((await self.client.get("/api/session")).json()["role"], "administrator")
        self.set_session(token)
        self.assertEqual(
            (
                await self.client.post(
                    "/api/security/password",
                    json={"password": "this cannot change the administrator"},
                )
            ).status_code,
            403,
        )

    async def test_profiles_and_labels_do_not_cross_mailboxes_or_recreated_addresses(self):
        await self.login_mailbox()
        initial = (await self.client.get("/api/profile")).json()
        fields = {
            "display_name": "Private user",
            "reply_to": "reply@example.net",
            "signature": "hello\nthere",
            "theme": "dark",
        }
        self.assertEqual((await self.client.put("/api/profile", json=fields)).status_code, 200)
        created = await self.client.post(
            "/api/webmail/labels", json={"name": "Private label", "color": "#123456"}
        )
        label = created.json()["id"]
        await self.login_mailbox("second@rsalehin24.me")
        self.assertEqual((await self.client.get("/api/profile")).json()["display_name"], "")
        self.assertEqual((await self.client.get("/api/webmail/labels")).json(), [])
        self.assertEqual(
            (
                await self.client.put(
                    "/api/webmail/labels/" + label, json={"name": "stolen", "color": "#000000"}
                )
            ).status_code,
            404,
        )
        self.fixture.imap.accounts["first@rsalehin24.me"][1] += 1
        denied = await self.client.post(
            "/api/login", json={"email": "first@rsalehin24.me", "password": MAILBOX_PASSWORD}
        )
        self.assertEqual(denied.status_code, 409)
        from backend.webmail_schema import retire

        with self.fixture.database.transaction() as connection:
            retire(connection, "first@rsalehin24.me")
        self.fixture.services.webmail.auth.cleanup(force=True)
        await self.login_mailbox()
        fresh = (await self.client.get("/api/profile")).json()
        self.assertNotEqual(fresh["id"], initial["id"])
        self.assertEqual(fresh["display_name"], "")
        self.assertEqual((await self.client.get("/api/webmail/labels")).json(), [])
        forbidden = await self.client.put(
            "/api/profile", json={**fields, "email": "other@example.net"}
        )
        self.assertEqual(forbidden.status_code, 422)
        self.assertNotIn("notes", json.dumps(fresh))

    async def test_expiry_logout_and_restart_remove_session_credentials(self):
        token = await self.login_mailbox()
        auth = self.fixture.services.webmail.auth
        self.assertEqual(len(auth.vault), 1)
        self.assertNotIn(MAILBOX_PASSWORD, repr(auth.vault))
        self.assertNotIn(MAILBOX_PASSWORD.encode(), self.fixture.database.path.read_bytes())
        self.fixture.now += 901
        self.assertEqual((await self.client.get("/api/profile")).status_code, 401)
        self.assertEqual(auth.vault, {})
        await self.login_mailbox()
        self.assertEqual((await self.client.post("/api/logout", json={})).status_code, 200)
        self.assertEqual(auth.vault, {})
        await self.login_mailbox()
        replacement = MailServices(self.fixture.auth, self.fixture.imap)
        self.addCleanup(replacement.close)
        with self.assertRaises(ServiceError):
            replacement.auth.require(token)
        self.assertEqual(replacement.auth.vault, {})

    async def test_failed_account_delete_preserves_authenticator_and_completed_delete_retires(self):
        token = await self.login_mailbox()
        auth = self.fixture.services.webmail.auth
        identity = auth.require(token)
        with self.fixture.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_users SET totp_secret=?,display_name=? WHERE id=?",
                (auth.keys.seal("protected authenticator", identity.id), "Retained", identity.id),
            )
        payload = {
            "request_id": str(uuid.uuid4()),
            "confirmation": identity.email,
            "acknowledged": True,
            "cutoff": "validated by helper",
        }
        with patch.object(
            self.fixture.services.mailboxes.helper,
            "call",
            side_effect=ServiceError("helper_busy", 503),
        ):
            with self.assertRaises(ServiceError):
                self.fixture.services.mailboxes.delete(identity.email, payload)
        with self.fixture.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mailbox_users WHERE id=?", (identity.id,)
            ).fetchone()
            self.assertEqual(row["active"], 1)
            self.assertTrue(row["totp_secret"])
            self.assertEqual(row["display_name"], "Retained")
        self.assertEqual(auth.vault, {})
        with patch.object(
            self.fixture.services.mailboxes.helper, "call", return_value={"state": "complete"}
        ):
            self.fixture.services.mailboxes.delete(identity.email, payload)
        await self.login_mailbox()
        fresh = (await self.client.get("/api/profile")).json()
        self.assertNotEqual(fresh["id"], identity.id)
        self.assertFalse(fresh["two_step_enabled"])
        self.assertEqual(fresh["display_name"], "")

    async def test_optional_totp_recovery_single_use_and_security_proof(self):
        token = await self.login_mailbox()
        proof = {"password": MAILBOX_PASSWORD, "factor": "totp", "code": ""}
        start = await self.client.post(
            "/api/profile/authenticator", json={**proof, "action": "begin"}
        )
        secret = start.json()["secret"]
        self.assertIn("first%40rsalehin24.me", start.json()["uri"])
        confirm = await self.client.post(
            "/api/profile/authenticator",
            json={
                **proof,
                "action": "confirm",
                "new_code": pyotp.TOTP(secret).at(self.fixture.now),
            },
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        codes = confirm.json()["recovery_codes"]
        self.assertEqual(len(codes), 5)
        self.set_session(token)
        self.assertEqual((await self.client.get("/api/profile")).status_code, 401)
        challenge = await self.client.post(
            "/api/login", json={"email": "first@rsalehin24.me", "password": MAILBOX_PASSWORD}
        )
        self.assertIn("challenge", challenge.json())
        verification = {
            "challenge": challenge.json()["challenge"],
            "factor": "recovery",
            "code": codes[0],
        }
        self.assertEqual(
            (await self.client.post("/api/2fa/verify", json=verification)).status_code, 200
        )
        self.assertEqual(
            (await self.client.post("/api/2fa/verify", json=verification)).status_code, 401
        )
        for code in ("", codes[0]):
            invalid = await self.client.post(
                "/api/profile/authenticator",
                json={**proof, "code": code, "factor": "recovery", "action": "disable"},
            )
            self.assertEqual(invalid.status_code, 401)
        disabled = await self.client.post(
            "/api/profile/authenticator",
            json={**proof, "code": codes[1], "factor": "recovery", "action": "disable"},
        )
        self.assertEqual(disabled.status_code, 200)
        await self.login_mailbox()
        self.assertFalse((await self.client.get("/api/profile")).json()["two_step_enabled"])

    async def test_password_change_requires_current_password_and_revokes_all_sessions(self):
        first = await self.login_mailbox()
        second = await self.login_mailbox()
        response = await self.client.post(
            "/api/profile/password",
            json={
                "password": "wrong password",
                "new_password": "replacement mailbox password",
            },
        )
        self.assertEqual(response.status_code, 401)
        with patch.object(
            self.fixture.services.mailboxes, "reset", return_value={"state": "complete"}
        ) as reset:
            response = await self.client.post(
                "/api/profile/password",
                json={
                    "password": MAILBOX_PASSWORD,
                    "new_password": "replacement mailbox password",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(reset.call_args.args[0], "first@rsalehin24.me")
        for token in (first, second):
            self.set_session(token)
            self.assertEqual((await self.client.get("/api/profile")).status_code, 401)
        self.assertEqual(self.fixture.services.webmail.auth.vault, {})

    async def test_upload_stream_limits_ownership_and_delete(self):
        await self.login_mailbox()
        response = await self.client.put(
            "/api/webmail/uploads?filename=../../unsafe.txt",
            content=b"synthetic attachment",
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        upload = response.json()
        self.assertNotIn("/", upload["filename"])
        await self.login_mailbox("second@rsalehin24.me")
        response = await self.client.delete(
            "/api/webmail/uploads/" + upload["id"], headers={"Content-Type": "application/json"}
        )
        self.assertEqual(response.status_code, 404)
        response = await self.client.put(
            "/api/webmail/uploads?filename=large",
            content=b"x" * (10 * 1024 * 1024 + 1),
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(len(self.fixture.services.webmail.files.uploads), 1)

    async def test_feature_flag_blocks_mailbox_login_and_oversized_bulk(self):
        await self.login_mailbox()
        ref = {"folder": "INBOX", "uidvalidity": 1234, "uid": 1}
        response = await self.client.post(
            "/api/webmail/messages/bulk", json={"messages": [ref] * 101, "action": "read"}
        )
        self.assertEqual(response.status_code, 422)
        self.fixture.services.settings = dataclasses.replace(
            self.fixture.services.settings, webmail_enabled=False
        )
        self.assertEqual((await self.client.get("/api/profile")).status_code, 503)
        self.assertEqual(
            (
                await self.client.post(
                    "/api/login",
                    json={"email": "first@rsalehin24.me", "password": MAILBOX_PASSWORD},
                )
            ).status_code,
            401,
        )

    async def test_search_deadline_includes_dependencies_and_preserves_worker_capacity(self):
        await self.login_mailbox()
        release = threading.Event()
        entered = threading.Event()

        def slow_search(*args):
            entered.set()
            release.wait(30)
            return {"messages": [], "total": 0}

        try:
            with (
                patch("backend.boundary.SEARCH_DEADLINE_SECONDS", 0.2),
                patch.object(
                    self.fixture.services.webmail.mail, "messages", side_effect=slow_search
                ),
            ):
                response = await asyncio.wait_for(
                    self.client.post("/api/webmail/messages/search", json={}), 1
                )
            self.assertEqual(response.status_code, 504)
            self.assertEqual(response.json()["error"], "search_timeout")
            self.assertTrue(entered.is_set())
            pool = self.fixture.services.webmail.pool.pool
            self.assertTrue(pool.capacity.acquire(blocking=False))
            self.assertTrue(pool.capacity.acquire(blocking=False))
            self.assertFalse(pool.capacity.acquire(blocking=False))
            pool.capacity.release()
            pool.capacity.release()
        finally:
            release.set()

    async def test_search_queue_deadline_returns_an_explicit_timeout(self):
        await self.login_mailbox()
        pool = self.fixture.services.webmail.pool
        release = threading.Event()
        started = threading.Semaphore(0)

        def occupied():
            started.release()
            release.wait(10)

        tasks = [asyncio.create_task(pool.run(occupied)) for _ in range(3)]
        try:
            for _ in tasks:
                self.assertTrue(await asyncio.to_thread(started.acquire, timeout=1))
            with patch.object(pool, "admission_timeout", 0.05):
                response = await asyncio.wait_for(
                    self.client.post("/api/webmail/messages/search", json={}), 1
                )
            self.assertEqual(response.status_code, 504)
            self.assertEqual(response.json()["error"], "search_timeout")
        finally:
            release.set()
            await asyncio.gather(*tasks)
