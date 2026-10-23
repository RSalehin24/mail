import os
import unittest
from unittest.mock import patch

from backend.services import Services
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RuntimePolicy
from tests.support import StateFixture


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.marker, self.path = (
            self.fixture.root / "marker.json",
            self.fixture.root / "policy.json",
        )
        atomic_json(self.marker, {"installation_id": "synthetic-installation"})
        atomic_json(
            self.path,
            {
                "installation_id": "synthetic-installation",
                "intent": "expected_running",
            },
        )
        self.policy = RuntimePolicy(self.marker, self.path, os.getuid())

    def test_pause_is_durable_and_an_explicit_resume_preserves_installation_identity(self):
        self.policy.require_running()
        parent_inode = self.path.parent.stat().st_ino
        self.policy.pause()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
            RuntimePolicy(self.marker, self.path, os.getuid()).require_running()
        self.policy.resume()
        self.policy.require_running()
        self.assertEqual(self.path.parent.stat().st_ino, parent_inode)
        self.assertEqual(self.policy.read()["installation_id"], "synthetic-installation")

    def test_group_access_is_set_before_atomic_publication(self):
        original = os.replace

        def publish(source, destination):
            metadata = os.stat(source)
            self.assertEqual(metadata.st_mode & 0o777, 0o640)
            self.assertEqual(metadata.st_gid, self.path.stat().st_gid)
            original(source, destination)

        with patch("shared.files.os.replace", side_effect=publish):
            self.policy.pause()

    def test_missing_or_mismatched_policy_never_creates_an_open_replacement(self):
        self.path.rename(self.path.with_suffix(".old"))
        with self.assertRaises(OSError):
            self.policy.require_running()
        self.assertFalse(self.path.exists())
        atomic_json(
            self.path, {"installation_id": "another-installation", "intent": "expected_running"}
        )
        with self.assertRaisesRegex(ServiceError, "runtime_policy_identity_mismatch"):
            self.policy.require_running()
        self.assertEqual(
            protected_json(self.path, os.getuid())["installation_id"], "another-installation"
        )

    def test_invalid_or_world_readable_policy_fails_closed(self):
        for intent in (None, True, {}, [], "invented"):
            atomic_json(self.path, {"installation_id": "synthetic-installation", "intent": intent})
            with self.assertRaisesRegex(ServiceError, "invalid_runtime_intent"):
                self.policy.require_running()
        atomic_json(
            self.path, {"installation_id": "synthetic-installation", "intent": "expected_running"}
        )
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ServiceError, "unsafe_file_mode"):
            self.policy.require_running()

    def test_closed_policy_is_checked_before_application_state_or_workers_are_loaded(self):
        with (
            patch(
                "backend.services.RuntimePolicy.require_running",
                side_effect=ServiceError("operator_maintenance", 503),
            ),
            patch.object(Services, "_load") as load,
        ):
            with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
                Services.production()
        load.assert_not_called()

    def test_unprivileged_web_user_cannot_use_the_operator_composition_root(self):
        with (
            patch("backend.services.os.geteuid", return_value=10001),
            patch.object(Services, "_load") as load,
        ):
            with self.assertRaisesRegex(ServiceError, "root_required"):
                Services.operator()
        load.assert_not_called()
