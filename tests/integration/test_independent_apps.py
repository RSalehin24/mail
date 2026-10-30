"""Independent application security and pairing lifecycle checks; no production access."""

import base64
import json
import unittest

import httpx

from applications import CAPABILITIES
from applications.certificates import ca, csr, private_key
from applications.identity import Identities
from applications.integration_api import create_integration_app
from applications.pairing import PairingAuthority
from applications.runtime import create_app
from applications.schema import install
from backend.authorization import COOKIE_NAME
from backend.webmail_services import MailServices
from deployment.recovery import invalidate
from shared.errors import ServiceError
from tests.api_support import ApiFixture
from tests.fixture_values import MAILBOX_PASSWORD
from tests.webmail_support import IdentityImap


class NativeIdentity:
    def __init__(self):
        self.accounts = {"first@rsalehin24.me": {"native_id": 1, "inbox_validity": 1234}}

    def call(self, request):
        if request["operation"] != "mailbox_identity":
            raise ServiceError("unsupported", 400)
        if request["email"] not in self.accounts:
            raise ServiceError("account_not_found", 404)
        return self.accounts[request["email"]]


class RemoteIdentity:
    def __init__(self):
        self.identity = {
            "id": "a" * 32,
            "email": "first@rsalehin24.me",
            "revision": 0,
            "inbox_validity": 1234,
        }
        self.failure = None

    def identify(self, email):
        if self.failure:
            raise self.failure
        return dict(self.identity)


class IndependentApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = ApiFixture()
        self.addCleanup(self.fixture.close)
        self.database = self.fixture.database
        install(self.database, "management")
        self.native = NativeIdentity()
        self.identities = Identities(self.database, self.native)
        key, certificate = ca("test integration")
        self.authority = PairingAuthority(self.database, self.fixture.keys, key, certificate)
        self.peer = ""
        application = create_integration_app(
            self.authority, self.identities, self.fixture.services.mailboxes, IdentityImap()
        )

        async def transport(scope, receive, send):
            if self.peer:
                scope.setdefault("state", {})["client_certificate"] = self.peer
            await application(scope, receive, send)

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=transport), base_url="https://mail.rsalehin24.me"
        )
        self.addAsyncCleanup(self.client.aclose)

    async def pair(self):
        result = self.authority.enrollment("https://mail.rsalehin24.me:8444")
        invitation = json.loads(base64.urlsafe_b64decode(result["enrollment_token"][4:]))
        with self.database.transaction() as connection:
            self.peer = connection.execute(
                "SELECT fingerprint FROM integration_enrollments"
            ).fetchone()[0]
        key = private_key()
        payload = {
            "api_version": 1,
            "capabilities": list(CAPABILITIES),
            "installation_id": "00000000-0000-4000-8000-000000000002",
            "token": invitation["token"],
            "csr": csr(key, "webmail"),
        }
        response = await self.client.post("/integration/v1/enroll", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        with self.database.transaction() as connection:
            self.peer = connection.execute(
                "SELECT fingerprint FROM integration_credentials WHERE id=?",
                (result["credential_id"],),
            ).fetchone()[0]
        self.client.headers.update(
            {"Authorization": "Bearer " + result["bearer"], "X-Mail-Integration-Version": "1"}
        )
        return result, payload

    async def test_transport_identity_cannot_be_forged_by_header(self):
        response = await self.client.get(
            "/integration/v1/status", headers={"X-Client-Certificate": "a" * 64}
        )
        self.assertEqual(response.status_code, 403)

    async def test_integration_body_is_bounded_before_schema_parsing(self):
        self.peer = "verified transport"
        response = await self.client.post(
            "/integration/v1/enroll",
            content=b"x" * 65537,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"], "integration_body_limit")

    async def test_pairing_identity_and_domain_scope(self):
        await self.pair()
        response = await self.client.post(
            "/integration/v1/identity", json={"email": "first@rsalehin24.me"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        identifier = response.json()["id"]
        self.assertEqual(len(identifier), 32)
        response2 = await self.client.post(
            "/integration/v1/identity", json={"email": "first@another.test"}
        )
        self.assertEqual(response2.status_code, 400)
        again = await self.client.post(
            "/integration/v1/identity", json={"email": "first@rsalehin24.me"}
        )
        self.assertEqual(again.json()["id"], identifier)

    async def test_single_use_enrollment(self):
        _, payload = await self.pair()
        with self.database.transaction() as connection:
            self.peer = connection.execute(
                "SELECT fingerprint FROM integration_enrollments"
            ).fetchone()[0]
        response = await self.client.post("/integration/v1/enroll", json=payload)
        self.assertEqual(response.status_code, 403)

    async def test_certificate_must_match_bearer(self):
        await self.pair()
        self.peer = "0" * 64
        response = await self.client.get("/integration/v1/status")
        self.assertEqual(response.status_code, 401)

    async def test_incompatible_version_blocks_operation(self):
        await self.pair()
        response = await self.client.get(
            "/integration/v1/status", headers={"X-Mail-Integration-Version": "2"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "integration_upgrade_required")

    async def test_service_credential_cannot_open_mail_or_reset_without_password(self):
        await self.pair()
        identity = self.identities.lookup("first@rsalehin24.me")
        response = await self.client.get("/api/webmail/folders")
        self.assertEqual(response.status_code, 404)
        response = await self.client.post(
            "/integration/v1/password",
            json={
                "email": identity["email"],
                "mailbox_id": identity["id"],
                "revision": 0,
                "request_id": "00000000-0000-4000-8000-000000000003",
                "password": "wrong password",
                "new_password": MAILBOX_PASSWORD,
            },
        )
        self.assertEqual(response.status_code, 401)

    async def test_authenticated_mailbox_password_change_advances_revision(self):
        await self.pair()
        identity = self.identities.lookup("first@rsalehin24.me")
        maddy = self.fixture.host.maddy
        maddy.credentials.add(identity["email"])
        maddy.storage.add(identity["email"])
        maddy.require_mailbox_identity = lambda email, validity: None
        self.fixture.services.mailboxes.identities = self.identities
        self.fixture.services.mailboxes.ownership = self.identities.guard
        response = await self.client.post(
            "/integration/v1/password",
            json={
                "email": identity["email"],
                "mailbox_id": identity["id"],
                "revision": 0,
                "request_id": "00000000-0000-4000-8000-000000000003",
                "password": MAILBOX_PASSWORD,
                "new_password": "new synthetic mailbox password",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "complete")
        self.assertGreater(
            self.identities.lookup(identity["email"])["revision"], identity["revision"]
        )
        self.assertEqual(maddy.calls[-1][1], "new synthetic mailbox password")

    async def test_rotation_preparation_activation_and_revocation(self):
        original, _ = await self.pair()
        original_peer = self.peer
        response = await self.client.post(
            "/integration/v1/credentials/rotate", json={"csr": csr(private_key(), "new peer")}
        )
        self.assertEqual(response.status_code, 200, response.text)
        replacement = response.json()
        self.client.headers["Authorization"] = "Bearer " + replacement["bearer"]
        with self.database.transaction() as connection:
            self.peer = connection.execute(
                "SELECT fingerprint FROM integration_credentials WHERE id=?",
                (replacement["credential_id"],),
            ).fetchone()[0]
        self.assertEqual((await self.client.get("/integration/v1/status")).status_code, 409)
        self.assertEqual(
            (await self.client.post("/integration/v1/credentials/activate", json={})).status_code,
            200,
        )
        self.assertEqual((await self.client.get("/integration/v1/status")).status_code, 200)
        self.client.headers["Authorization"] = "Bearer " + original["bearer"]
        self.peer = original_peer
        self.assertEqual((await self.client.get("/integration/v1/status")).status_code, 401)
        self.authority.revoke(replacement["credential_id"])
        self.client.headers["Authorization"] = "Bearer " + replacement["bearer"]
        self.assertEqual((await self.client.get("/integration/v1/status")).status_code, 401)

    async def test_revocation_cannot_be_undone_by_a_previously_authorized_rotation(self):
        original, _ = await self.pair()
        authorized = self.authority.authenticate(original["bearer"], self.peer)
        prepared = self.authority.rotate(authorized, csr(private_key(), "prepared"))
        with self.database.transaction() as connection:
            fingerprint = connection.execute(
                "SELECT fingerprint FROM integration_credentials WHERE id=?",
                (prepared["credential_id"],),
            ).fetchone()[0]
        pending = self.authority.authenticate(prepared["bearer"], fingerprint)
        self.authority.revoke(original["credential_id"])
        with self.assertRaises(ServiceError):
            self.authority.rotate(authorized, csr(private_key(), "too late"))
        with self.assertRaises(ServiceError):
            self.authority.activate(pending)

    async def test_recreation_changes_identity_and_password_change_blocks_checks(self):
        before = self.identities.lookup("first@rsalehin24.me")
        self.identities.changing(before["email"])
        with self.assertRaisesRegex(ServiceError, "mailbox_security_change_pending"):
            self.identities.lookup(before["email"])
        self.identities.changed(before["email"], True)
        after = self.identities.lookup(before["email"])
        self.assertEqual(before["id"], after["id"])
        self.assertGreater(after["revision"], before["revision"])
        self.identities.changing(before["email"], retire=True)
        self.native.accounts[before["email"]] = {"native_id": 2, "inbox_validity": 5678}
        self.assertNotEqual(self.identities.lookup(before["email"])["id"], before["id"])

    async def test_no_identity_guess_after_uidvalidity_change(self):
        before = self.identities.lookup("first@rsalehin24.me")
        self.native.accounts[before["email"]]["inbox_validity"] += 1
        with self.assertRaisesRegex(ServiceError, "mailbox_identity_reconciliation_required"):
            self.identities.lookup(before["email"])

    async def test_webmail_administrator_has_no_management_or_mail_access(self):
        from dataclasses import replace

        self.fixture.services.settings = replace(
            self.fixture.services.settings, application_kind="webmail"
        )
        app = create_app(self.fixture.services, self.fixture.root)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://mail.rsalehin24.me",
            headers={"Origin": "https://mail.rsalehin24.me"},
        )
        self.addAsyncCleanup(client.aclose)
        client.cookies.set(COOKIE_NAME, self.fixture.login(), domain="mail.rsalehin24.me")
        self.assertEqual((await client.get("/api/accounts")).status_code, 404)
        self.assertEqual((await client.get("/api/webmail/folders")).status_code, 503)


class RemoteSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ApiFixture()
        self.addCleanup(self.fixture.close)
        install(self.fixture.database, "webmail")
        self.remote = RemoteIdentity()
        self.mail = MailServices(self.fixture.auth, IdentityImap(), self.remote)
        self.addCleanup(self.mail.close)
        self.result, self.token = self.mail.auth.login(
            "first@rsalehin24.me", MAILBOX_PASSWORD, "peer"
        )

    def test_profile_bound_to_authoritative_id(self):
        self.assertEqual(self.result["user"]["id"], self.remote.identity["id"])
        self.assertEqual(self.mail.auth.require(self.token).id, self.remote.identity["id"])

    def test_unavailable_identity_fails_closed(self):
        self.remote.failure = ServiceError("management_connection_unavailable", 503)
        with self.assertRaisesRegex(ServiceError, "management_connection_unavailable"):
            self.mail.auth.profile(self.token)

    def test_cross_server_security_revision_invalidates_session(self):
        self.remote.identity["revision"] += 1
        with self.assertRaisesRegex(ServiceError, "authentication_required"):
            self.mail.auth.require(self.token)
        self.assertFalse(self.mail.auth.vault)
        _, token = self.mail.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "peer")
        self.assertEqual(self.mail.auth.require(token).id, self.remote.identity["id"])

    def test_recreated_account_cannot_inherit_profile(self):
        old = self.mail.auth.require(self.token)
        with self.fixture.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_users SET display_name='Private name' WHERE id=?", (old.id,)
            )
        self.remote.identity["id"] = "b" * 32
        with self.assertRaisesRegex(ServiceError, "authentication_required"):
            self.mail.auth.require(self.token)
        _, token = self.mail.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "peer")
        self.assertEqual(self.mail.auth.profile(token)["display_name"], "")

    def test_verified_same_owner_uidvalidity_change_preserves_profile(self):
        with self.fixture.database.transaction() as connection:
            connection.execute(
                "UPDATE mailbox_users SET display_name='Preserved' WHERE id=?",
                (self.remote.identity["id"],),
            )
        self.remote.identity["inbox_validity"] = 9876
        self.remote.identity["revision"] += 1
        self.mail.auth.imap.accounts["first@rsalehin24.me"][1] = 9876
        with self.assertRaises(ServiceError):
            self.mail.auth.require(self.token)
        _, token = self.mail.auth.login("first@rsalehin24.me", MAILBOX_PASSWORD, "peer")
        self.assertEqual(self.mail.auth.profile(token)["display_name"], "Preserved")
        self.assertEqual(self.mail.auth.require(token).validity, 9876)

    def test_restoration_invalidates_sessions_and_pending_sends(self):
        with self.fixture.database.transaction() as connection:
            connection.execute(
                "INSERT INTO mailbox_sends(id,user_id,state,created,updated) "
                "VALUES('send',?,'submitting',0,0)",
                (self.result["user"]["id"],),
            )
        invalidate(self.fixture.database)
        with self.assertRaises(ServiceError):
            self.mail.auth.require(self.token)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT state FROM mailbox_sends").fetchone()[0],
                "submission_uncertain",
            )
            self.assertEqual(
                connection.execute("SELECT cleanup_enabled FROM application_state").fetchone()[0], 0
            )
