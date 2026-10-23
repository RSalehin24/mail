import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from host.image_tools import ImageTools
from host.restore import Restore, verify_database
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from tests.support import StateFixture


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.destination = self.fixture.root / "new-private-restore"
        self.destination.mkdir(mode=0o700)
        operator = self.destination / "operator"
        operator.mkdir()
        (operator / "secrets").mkdir()
        (self.destination / "dashboard").mkdir()
        self.policy = RuntimePolicy(operator / "installation.json",
                                    operator / "secrets" / RUNTIME_POLICY_NAME, os.getuid())
        atomic_json(self.policy.marker, {"installation_id": "restore-fixture"})
        atomic_json(self.policy.path, {"installation_id": "restore-fixture",
                                       "intent": "expected_running"})
        self.result_path = self.destination.with_name(self.destination.name + ".restore.json")
        repository = Mock(process=SimpleNamespace(owner=os.getuid()))
        repository.restore.return_value = {"content_sha256": "a" * 64}
        self.restore = Restore(repository)

    def test_restored_state_is_closed_before_staging_returns_and_preparation_keeps_it_closed(self):
        result = self.restore.stage("fixture-reference", self.destination)
        self.assertEqual(result["state"], "authentication_closed")
        self.assertFalse(result["authentication_prepared"])
        self.assertFalse(result["services_started"])
        self.assertFalse(result["full_state_sources_present"])
        with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
            self.policy.require_running()
        tools = Mock()
        result = self.restore.prepare_authentication(self.destination, tools)
        tools.prepare_restored_authentication.assert_called_once_with(
            self.destination, result["utility_name"],
        )
        self.assertEqual(result["state"], "prepared_requires_ssh_recovery")
        self.assertEqual(self.policy.read()["intent"], "maintenance")

    def test_preparation_failure_remains_closed_and_cannot_report_prepared(self):
        self.restore.stage("fixture-reference", self.destination)
        tools = Mock()
        tools.prepare_restored_authentication.side_effect = ServiceError("utility_failed")
        with self.assertRaisesRegex(ServiceError, "utility_failed"):
            self.restore.prepare_authentication(self.destination, tools)
        result = protected_json(self.result_path, os.getuid())
        self.assertEqual(result["state"], "authentication_preparing")
        self.assertFalse(result["authentication_prepared"])
        self.assertEqual(self.policy.read()["intent"], "maintenance")
        tools.verify_utility_exit.side_effect = ServiceError("recovery_completion_unknown")
        with self.assertRaisesRegex(ServiceError, "recovery_completion_unknown"):
            self.restore.prepare_authentication(self.destination, tools)
        self.assertEqual(tools.prepare_restored_authentication.call_count, 1)
        tools.verify_utility_exit.side_effect = None
        completed = self.restore.prepare_authentication(self.destination, tools)
        self.assertTrue(completed["authentication_prepared"])
        tools.verify_utility_exit.assert_called_with(result["utility_name"])
        self.assertEqual(tools.prepare_restored_authentication.call_count, 1)

    def test_preparation_refuses_unrelated_staging_without_invoking_docker(self):
        self.restore.stage("fixture-reference", self.destination)
        atomic_json(self.result_path, {"state": "verified_staging",
                                       "destination": str(self.destination)})
        tools = Mock()
        with self.assertRaisesRegex(ServiceError, "closed_restore_staging_required"):
            self.restore.prepare_authentication(self.destination, tools)
        tools.prepare_restored_authentication.assert_not_called()

    def test_interrupted_ssh_recovery_only_observes_the_existing_utility_and_stays_closed(self):
        self.restore.stage("fixture-reference", self.destination)
        tools = Mock()
        self.restore.prepare_authentication(self.destination, tools)
        tools.offline_utility.side_effect = ServiceError("lost_terminal")
        with patch("host.restore.require_private_terminal"):
            with self.assertRaisesRegex(ServiceError, "lost_terminal"):
                self.restore.recover_authentication(self.destination, tools)
            interrupted = protected_json(self.result_path, os.getuid())
            self.assertEqual(interrupted["state"], "recovery_in_progress")
            tools.verify_recovery.side_effect = ServiceError("recovery_completion_unknown")
            with self.assertRaisesRegex(ServiceError, "recovery_completion_unknown"):
                self.restore.recover_authentication(self.destination, tools)
            tools.verify_recovery.side_effect = None
            completed = self.restore.recover_authentication(self.destination, tools)
        self.assertEqual(tools.offline_utility.call_count, 1)
        tools.verify_recovery.assert_called_with(interrupted["recovery_utility_name"],
                                                self.destination)
        self.assertEqual(completed["state"], "credentials_recovered_requires_activation")
        self.assertFalse(completed["services_started"])
        self.assertEqual(self.policy.read()["intent"], "maintenance")

    def test_nonterminal_restore_recovery_changes_no_state_and_launches_nothing(self):
        self.restore.stage("fixture-reference", self.destination)
        before = self.result_path.read_bytes()
        tools = Mock()
        with patch("host.image_tools.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(ServiceError, "private_terminal_required"):
                self.restore.recover_authentication(self.destination, tools)
        self.assertEqual(self.result_path.read_bytes(), before)
        tools.offline_utility.assert_not_called()

    def test_database_checks_quote_the_path_and_reject_corruption(self):
        path = self.fixture.root / "state?mode=memory.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE example (value INTEGER)")
        verify_database(path)
        path.write_bytes(b"corrupt fixture database")
        with self.assertRaisesRegex(ServiceError, "restored_database_invalid"):
            verify_database(path)

    def test_matched_utility_mounts_only_restored_state_and_never_opens_network_or_ports(self):
        deployment = {
            "dashboard_image_id": "sha256:" + "a" * 64,
            "inputs": {"service_uid": 10001, "service_gid": 10001},
            "consumers": {"mail-admin": {"environment": {"HASH_MEMORY_KIB": "8192"}}},
            "transient_resources": {"memory_limit_bytes": 128 * 1024 * 1024,
                                    "tmpfs_limit_bytes": 8 * 1024 * 1024, "cpu_limit": 0.5},
        }
        docker = Mock()
        docker.run.side_effect = ["", "fixture-container-id", ""]
        docker.inspect.return_value = {"Architecture": "amd64", "Os": "linux",
                                       "Config": {"User": "10001:10001"}}
        tools = ImageTools(docker, deployment)
        name = "mail-admin-recovery-3f1d67d6-994b-4d7e-8b12-c7877b29f91e"
        with patch.object(tools, "verify_utility_exit") as verify:
            tools.prepare_restored_authentication(self.destination, name)
            verify.assert_called_once_with(name)
        arguments = docker.run.call_args_list[1].args[0]
        self.assertIn("restore-prepare", arguments)
        self.assertEqual(arguments[arguments.index("--network") + 1], "none")
        self.assertNotIn("-p", arguments)
        self.assertNotIn("--rm", arguments)
        self.assertEqual(arguments[:3], ("create", "--name", name))
        mounts = [value for value in arguments if value.startswith("type=bind,src=")]
        self.assertEqual(len(mounts), 3)
        self.assertTrue(all(str(self.destination) in value for value in mounts))
        deployment["dashboard_image_id"] = "latest"
        docker.reset_mock()
        with self.assertRaisesRegex(ServiceError, "immutable_recovery_image_required"):
            tools.prepare_restored_authentication(self.destination, name)
        docker.run.assert_not_called()
