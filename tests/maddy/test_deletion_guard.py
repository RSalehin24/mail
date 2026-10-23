import hashlib
import time
import unittest
import uuid
from datetime import UTC, datetime

from helper.deletion import DeletionGuard, DeletionPaths
from helper.protocol import HelperRequest
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import HelperFixture


class DeletionGuardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        self.now = time.time()
        self.email = "guard-fixture@rsalehin24.me"
        inventory = {"credentials": [self.email], "storage": [self.email]}
        self.fixture.journal.synchronize(inventory, self.now - 60)
        incarnation = self.fixture.journal.incarnation(self.email)
        self.configuration = self.fixture.root / "maddy.conf"
        self.configuration.write_text("synthetic recipient configuration")
        self.paths = DeletionPaths(
            *(
                self.fixture.root / name
                for name in ("deployment.json", "policy.json", "backup.json")
            )
        )
        self.policy = {
            "valid_until": self.now + 300,
            "protected_recipients": ["postmaster@rsalehin24.me"],
            "reviewed_files": {
                str(self.configuration): hashlib.sha256(self.configuration.read_bytes()).hexdigest()
            },
        }
        self.write_policy()
        self.cutoff = datetime.fromtimestamp(self.now - 30, UTC).isoformat()
        self.manifest = {
            "verified": True,
            "available": True,
            "restore_passed": True,
            "availability_observed_at": self.now,
            "cutoff": self.cutoff,
            "accounts": {
                self.email: {
                    "generation": incarnation["generation"],
                    "credentials": True,
                    "storage": True,
                    "messages": True,
                }
            },
        }
        atomic_json(self.paths.backup, self.manifest)
        self.request = HelperRequest("delete", str(uuid.uuid4()), self.email, cutoff=self.cutoff)
        self.guard = DeletionGuard(self.paths, lambda: self.now)

    def write_policy(self):
        atomic_json(self.paths.policy, self.policy)
        self.deployment = {
            "inputs": {
                "permanent_deletion_enabled": True,
                "protected_policy_sha256": hashlib.sha256(
                    self.paths.policy.read_bytes()
                ).hexdigest(),
                "deletion_backup_max_age_seconds": 600,
            }
        }
        atomic_json(self.paths.deployment, self.deployment)

    def test_root_evidence_validates_and_forged_browser_cutoff_does_not(self):
        self.guard.check(self.request, self.fixture.journal)
        wrong = HelperRequest("delete", self.request.request_id, self.email, cutoff="invented")
        with self.assertRaises(ServiceError):
            self.guard.check(wrong, self.fixture.journal)

    def test_each_backup_requirement_is_independently_enforced(self):
        for key, value in (
            ("verified", False),
            ("available", False),
            ("restore_passed", False),
            ("availability_observed_at", self.now - 301),
            ("cutoff", datetime.fromtimestamp(self.now - 1000, UTC).isoformat()),
        ):
            with self.subTest(key=key):
                atomic_json(self.paths.backup, {**self.manifest, key: value})
                with self.assertRaises(ServiceError):
                    self.guard.check(self.request, self.fixture.journal)
        for key, value in (
            ("generation", "different-incarnation"),
            ("credentials", False),
            ("storage", False),
            ("messages", False),
        ):
            record = {
                **self.manifest,
                "accounts": {self.email: {**self.manifest["accounts"][self.email], key: value}},
            }
            atomic_json(self.paths.backup, record)
            with self.assertRaises(ServiceError):
                self.guard.check(self.request, self.fixture.journal)

    def test_disabled_changed_policy_protected_recipient_and_unsafe_mode_fail(self):
        self.policy["protected_recipients"].append(self.email)
        self.write_policy()
        with self.assertRaises(ServiceError) as caught:
            self.guard.check(self.request, self.fixture.journal)
        self.assertEqual(caught.exception.code, "protected_recipient")
        self.configuration.write_text("dependency changed after review")
        with self.assertRaises(ServiceError) as caught:
            self.guard.check(self.request, self.fixture.journal)
        self.assertEqual(caught.exception.code, "recipient_policy_stale")
        self.paths.deployment.chmod(0o644)
        with self.assertRaises(ServiceError):
            self.guard.capability()
