import json
import os
import time
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

import pyotp

from backend.auth import Verification
from helper.protocol import HelperRequest
from host.backup import CaptureDependencies, SnapshotCapture
from host.image_tools import ImageTools
from host.repository_backup import RepositoryBackup
from host.restore import Restore
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from tests.auth_support import PASSWORD
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture
from tests.recovery_terminal import recover_through_terminal
from tests.restic_support import RestoredFixtureDocker, local_repository
from tests.restore_support import DashboardRecoveryFixture, RecoveryArchive, bodies, deliver
from tests.restored_runtime_support import RestoredRuntimeFixture


def lose_preparation_completion(path, record):
    if record.get("state") == "prepared_requires_ssh_recovery":
        raise OSError("synthetic lost preparation result")
    atomic_json(path, record)


class NativeResticRestoreTests(unittest.TestCase):
    def create_repository(self, directory, staging, clock):
        return local_repository(directory, staging, clock)

    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository(self):
        email = "restic-recovery@rsalehin24.me"
        created = self.fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(created["state"], "complete")
        deliver(self.fixture, email, "native-restic-message-body")
        dashboard = DashboardRecoveryFixture(self.fixture.root)
        os.chown(self.fixture.tls, 0, 10001)
        self.fixture.tls.chmod(0o750)
        for path in self.fixture.tls.iterdir():
            os.chown(path, 0, 10001)
            path.chmod(0o640)
        deployment = {
            "dashboard_image_id": os.environ["MAIL_ADMIN_RUNTIME_IMAGE"],
            "inputs": {"service_uid": 10001, "service_gid": 10001},
            "consumers": {"mail-admin": {"environment": {
                "HASH_MEMORY_KIB": "8192", "HASH_PARALLELISM": "1",
                "HTTP_CONCURRENCY": "4", "OPERATION_TIMEOUT_SECONDS": "5",
            }}},
            "transient_resources": {"memory_limit_bytes": 128 * 1024 * 1024,
                                    "tmpfs_limit_bytes": 8 * 1024 * 1024, "cpu_limit": 0.5},
        }
        atomic_json(dashboard.operator / "deployment.json", deployment)
        archive = RecoveryArchive(self.fixture, dashboard, b"native-restic-message-body")
        repository = self.create_repository(
            self.fixture.root, archive.backup.paths.staging, time.time,
        )
        capture = SnapshotCapture(archive.backup.paths,
                                  CaptureDependencies(archive.writers, archive.writers, time.time))
        backup = RepositoryBackup(capture, repository)
        archive.snapshot = backup.capture()
        manifest = backup.transfer(archive.snapshot)
        self.assertFalse(manifest["restore_passed"])
        restored = self.fixture.root / "replacement-state"
        recovery = Restore(repository)
        staged = recovery.stage(manifest["reference"], restored)
        self.assertEqual(staged["state"], "authentication_closed")
        self.assertFalse(staged["services_started"])
        self.assertFalse(staged["full_state_sources_present"])
        tools = ImageTools(RestoredFixtureDocker(self.fixture),
                           protected_json(restored / "operator/deployment.json"))
        archive.writers.pause(["maddy"])
        try:
            with patch("host.restore.atomic_json", side_effect=lose_preparation_completion):
                with self.assertRaisesRegex(OSError, "synthetic lost preparation result"):
                    recovery.prepare_authentication(restored, tools)
            interrupted = protected_json(restored.with_name(restored.name + ".restore.json"))
            self.assertEqual(interrupted["state"], "authentication_preparing")
            self.addCleanup(tools.discard_recovery, interrupted["utility_name"])
            prepared = recovery.prepare_authentication(restored, tools)
        finally:
            archive.writers.resume(["maddy"])
        self.assertEqual(prepared["state"], "prepared_requires_ssh_recovery")
        policy = RuntimePolicy(restored / "operator/installation.json",
                               restored / "operator/secrets" / RUNTIME_POLICY_NAME)
        with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
            policy.require_running()
        authentication, relay = dashboard.load(restored)
        with authentication.database.transaction() as connection:
            self.assertEqual(connection.execute(
                "SELECT recovery_required FROM admin_auth WHERE id=1"
            ).fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM auth_challenges"
            ).fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE action='restore'"
            ).fetchone()[0], 1)
            verifiers = {
                row[0] for row in connection.execute("SELECT verifier FROM recovery_codes")
            }
            self.assertTrue(all(authentication.keys.digest(code, "recovery") not in verifiers
                                for code in dashboard.codes))
        self.assertFalse(relay.otp_ready())
        archive.restored = restored
        with archive.maddy() as connection:
            connection.login(email, MAILBOX_PASSWORD)
            self.assertIn(b"native-restic-message-body", bodies(connection))
        archive.writers.pause(["maddy"])
        try:
            self.verify_restored_https((recovery, tools), (restored, dashboard))
        finally:
            archive.writers.resume(["maddy"])

    def verify_restored_https(self, operation, state):
        restored, _ = state
        with closing(RestoredRuntimeFixture(restored)) as web:
            self.verify_recovered_https(web, (operation, state))

    def verify_recovered_https(self, web, contexts):
        operation, state = contexts
        recovery, tools = operation
        restored, dashboard = state
        with self.assertRaisesRegex(RuntimeError, "Runtime did not become healthy"):
            web.start()
        self.assertNotEqual(web.exited()["ExitCode"], 0)
        web.command(("rm", web.identifier))
        web.identifier = ""
        material = recover_through_terminal(recovery, tools, restored)
        outcome = protected_json(restored.with_name(restored.name + ".restore.json"))
        self.addCleanup(tools.discard_recovery, outcome["recovery_utility_name"])
        self.assertEqual(outcome["state"], "credentials_recovered_requires_activation")
        observed = tools.docker.inspect(outcome["recovery_utility_name"])
        self.assertEqual(observed["HostConfig"]["LogConfig"]["Type"], "none")
        authentication, relay = dashboard.load(restored)
        self.assertEqual(relay.reveal(), "fixture-encrypted-relay-secret")
        self.assertFalse(relay.otp_ready())
        with self.assertRaises(ServiceError):
            authentication.require_session(dashboard.session)
        with self.assertRaises(ServiceError):
            authentication.verify(
                Verification(dashboard.challenge, "recovery", dashboard.codes[1], "restored"),
                "login",
            )
        policy = RuntimePolicy(restored / "operator/installation.json",
                               restored / "operator/secrets" / RUNTIME_POLICY_NAME)
        with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
            policy.require_running()
        # This activation is confined to the synthetic restored web container.
        policy.resume()
        web.start()
        web.cookie = "mail_admin=" + dashboard.session
        self.assertEqual(web.api("GET", "/api/settings/relay")[0], 401)
        web.cookie = ""
        status, _, challenge = web.api("POST", "/api/login", {
            "email": "rsalehin24@gmail.com", "password": PASSWORD,
        })
        self.assertEqual(status, 200)
        status, headers, _ = web.api("POST", "/api/2fa/verify", {
            "challenge": challenge["challenge"], "factor": "totp",
            "code": pyotp.TOTP(material.seed).at(time.time() + 30),
        })
        self.assertEqual(status, 200)
        web.cookie = headers["set-cookie"].split(";", 1)[0]
        status, _, settings = web.api("GET", "/api/settings/relay")
        self.assertEqual(status, 200)
        self.assertFalse(settings["otp_ready"])
        self.assertNotIn("fixture-encrypted-relay-secret", json.dumps(settings))
