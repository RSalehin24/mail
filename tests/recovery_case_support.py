"""Shared real-mail capture, authenticated restore and guarded-deletion fixture."""

import dataclasses
import hashlib
import time
import unittest
import uuid

from backend.auth import Verification
from helper.deletion import DeletionGuard, DeletionPaths
from helper.journal import Journal
from helper.protocol import HelperRequest
from helper.service import HostHelper
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import DashboardRecoveryFixture, RecoveryArchive, bodies


class RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def create(self, local):
        email = local + "@rsalehin24.me"
        outcome = self.fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(outcome["state"], "complete")
        return email

    def configure_deletion(self, archive):
        root = self.fixture.root
        policy = root / "policy.json"
        atomic_json(
            policy,
            {
                "protected_recipients": ["postmaster@rsalehin24.me"],
                "valid_until": time.time() + 600,
                "reviewed_files": {
                    str(self.fixture.configuration): hashlib.sha256(
                        self.fixture.configuration.read_bytes()
                    ).hexdigest()
                },
            },
        )
        deployment = root / "deployment.json"
        atomic_json(
            deployment,
            {
                "inputs": {
                    "permanent_deletion_enabled": True,
                    "protected_policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
                    "deletion_backup_max_age_seconds": 600,
                }
            },
        )
        paths = DeletionPaths(deployment, policy, archive.backup.paths.manifest)
        helper = HostHelper(
            dataclasses.replace(
                self.fixture.helper.dependencies, deletion=DeletionGuard(paths, time.time)
            )
        )
        return helper

    def verify_dashboard(self, archive, original):
        authentication, relay = original.recover(archive.restored)
        self.assertEqual(relay.reveal(), "fixture-encrypted-relay-secret")
        self.assertFalse(relay.otp_ready())
        with self.assertRaises(ServiceError):
            authentication.require_session(original.session)
        with self.assertRaises(ServiceError):
            authentication.verify(
                Verification(original.challenge, "recovery", original.codes[1], "restored"), "login"
            )
        self.assertEqual((archive.restored / "dashboard/admin_data.db").stat().st_uid, 10001)
        restored_journal = Journal(archive.restored / "journal")
        self.assertFalse(restored_journal.unresolved())

    def assert_body(self, email, expected):
        connection = imap_login(self.fixture, email, MAILBOX_PASSWORD)
        with connection:
            self.assertIn(expected, bodies(connection))

    def verified_archive(self, target, body):
        dashboard = DashboardRecoveryFixture(self.fixture.root)
        archive = RecoveryArchive(self.fixture, dashboard, body)
        started = time.monotonic()
        archive.capture()
        self.verify_dashboard(archive, dashboard)
        with archive.maddy() as connection:
            connection.login(target, MAILBOX_PASSWORD)
            self.assertIn(body, bodies(connection))
        archive.manifest.update(
            {"restore_passed": True, "restore_elapsed_seconds": time.monotonic() - started}
        )
        atomic_json(archive.backup.paths.manifest, archive.manifest)
        return archive
