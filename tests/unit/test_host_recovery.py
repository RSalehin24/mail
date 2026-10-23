import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.image_tools import ImageTools
from host.locks import LockPolicy
from host.recovery import Recovery
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RuntimePolicy
from tests.support import StateFixture
from tests.unit.test_host_capture_alerts import SnapshotFixture


class RecoveryFixture:
    def __init__(self, policy, scope):
        self.policy, self.scope = policy, scope
        self.events = []
        self.children = {}
        self.running = True
        self.fail_utility = False

    def pause_runtime(self):
        self.policy.pause()
        self.events.append("runtime_closed")

    def stop(self):
        self.running = False
        self.events.append("web_stopped")

    def snapshot_authentication(self):
        assert not self.running
        self.events.append("snapshot")

    def offline_utility(self, name):
        assert not self.scope.locked
        self.events.append("utility")
        self.children[name] = "running" if self.fail_utility else "exited_zero"
        if self.fail_utility:
            raise ServiceError("synthetic_lost_terminal", 503)

    def discard_recovery(self, name):
        if self.children.get(name) == "running":
            raise ServiceError("operation_container_still_active", 409)
        self.children.pop(name, None)

    def verify_recovery(self, name):
        if self.children.get(name) != "exited_zero":
            raise ServiceError("recovery_completion_unknown", 409)
        self.events.append("completion_verified")

    def allow_runtime(self):
        self.policy.resume()
        self.events.append("resume_authorized")

    def verify_running(self):
        if not self.running:
            raise ServiceError("dashboard_resume_unverified", 503)


class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        marker, policy = self.fixture.root / "marker.json", self.fixture.root / "policy.json"
        atomic_json(marker, {"installation_id": "synthetic-recovery"})
        atomic_json(policy, {"installation_id": "synthetic-recovery", "intent": "expected_running"})
        self.policy = RuntimePolicy(marker, policy, os.getuid())
        state = self.fixture.root / "generations.json"
        atomic_json(
            state,
            {
                "coordination_migrated": True,
                "consumers": {"mail-admin": {"intent": "expected_running"}},
            },
        )
        self.generations = Generations(
            GenerationPaths(self.fixture.root, state),
            (
                CertificatePolicy(os.getgid(), os.getuid()),
                PairValidator(),
                lambda: self.fixture.now,
            ),
        )
        self.scope = SnapshotFixture()
        self.scope.locks = SimpleNamespace(policy=LockPolicy(0.03, os.getuid()))
        self.consumer = RecoveryFixture(self.policy, self.scope)
        self.outcome = self.fixture.root / "outcome.json"
        self.outcome.with_suffix(".lock").touch(mode=0o600)
        self.operation = Recovery(self.scope, (self.generations, self.consumer, self.outcome))

    def record(self):
        return protected_json(self.outcome, os.getuid())

    def test_runtime_is_closed_before_stopping_and_utility_runs_without_host_locks(self):
        self.operation.run()
        self.assertEqual(
            self.consumer.events, ["runtime_closed", "web_stopped", "snapshot", "utility"]
        )
        self.assertEqual(self.scope.events, ["renewal_then_maddy_locked", "unlocked"])
        self.assertEqual(self.record()["state"], "completed")
        self.assertTrue(self.record()["consumer_stopped"])
        self.assertEqual(self.policy.read()["intent"], "maintenance")
        self.assertEqual(
            self.generations.read()["consumers"]["mail-admin"]["intent"], "maintenance"
        )

    def test_interruption_before_docker_stop_still_blocks_web_startup(self):
        with patch.object(self.generations, "maintenance", side_effect=OSError("synthetic death")):
            with self.assertRaises(OSError):
                self.operation.run()
        with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
            self.policy.require_running()
        self.assertTrue(self.consumer.running)
        self.assertNotIn("utility", self.consumer.events)

    def test_a_surviving_utility_blocks_both_new_recovery_and_resume(self):
        self.consumer.fail_utility = True
        with self.assertRaises(ServiceError):
            self.operation.run()
        name = self.record()["utility_name"]
        with self.assertRaisesRegex(ServiceError, "operation_container_still_active"):
            self.operation.run()
        with self.assertRaisesRegex(ServiceError, "recovery_completion_unknown"):
            self.operation.resume(lambda: self.fail("Cannot activate a surviving writer"))
        self.assertEqual(self.record()["utility_name"], name)
        self.assertEqual(self.consumer.events.count("utility"), 1)
        self.assertEqual(self.policy.read()["intent"], "maintenance")

    def test_lost_completion_record_requires_observation_then_explicit_activation(self):
        self.consumer.fail_utility = True
        with self.assertRaises(ServiceError):
            self.operation.run()
        name = self.record()["utility_name"]
        self.consumer.children[name] = "exited_zero"
        with self.assertRaisesRegex(ServiceError, "dashboard_resume_unverified"):
            self.operation.resume(lambda: None)
        self.assertEqual(self.record()["state"], "resume_authorized")
        self.policy.require_running()
        self.assertFalse(self.consumer.running)

        def activate():
            self.assertFalse(self.scope.locked)
            self.consumer.running = True

        self.operation.resume(activate)
        self.assertEqual(self.record()["state"], "resumed")
        self.assertFalse(self.record()["consumer_stopped"])

    def test_explicit_new_recovery_replaces_only_an_observed_stopped_utility(self):
        self.consumer.fail_utility = True
        with self.assertRaises(ServiceError):
            self.operation.run()
        previous = self.record()["utility_name"]
        self.consumer.children[previous] = "exited_nonzero"
        self.consumer.fail_utility = False
        self.operation.run()
        self.assertNotIn(previous, self.consumer.children)
        self.assertNotEqual(self.record()["utility_name"], previous)
        self.assertEqual(len(self.consumer.children), 1)

    def test_parallel_recovery_cannot_enter_the_gap_before_container_creation(self):
        original = self.consumer.offline_utility
        inode = self.outcome.with_suffix(".lock").stat().st_ino

        def pending(name):
            self.assertFalse(self.scope.locked)
            with self.assertRaisesRegex(ServiceError, "maintenance_busy"):
                self.operation.run()
            original(name)

        with patch.object(self.consumer, "offline_utility", side_effect=pending):
            self.operation.run()
        self.assertEqual(self.consumer.events.count("utility"), 1)
        self.assertEqual(inode, self.outcome.with_suffix(".lock").stat().st_ino)

    def test_resume_keeps_the_private_lease_during_activation_without_self_deadlock(self):
        self.operation.run()

        def activate():
            self.assertFalse(self.scope.locked)
            with self.assertRaisesRegex(ServiceError, "maintenance_busy"):
                self.operation.run()
            self.consumer.running = True

        self.operation.resume(activate)
        self.assertEqual(self.record()["state"], "resumed")

    def test_rollback_to_legacy_pairing_blocks_recovery_activation_and_new_recovery(self):
        self.operation.run()
        state = self.generations.read()
        state["coordination_migrated"] = False
        self.generations.write(state)
        with self.assertRaisesRegex(ServiceError, "matched_renewal_cutover_required"):
            self.operation.resume(lambda: self.fail("Legacy pairing cannot activate generations"))
        with self.assertRaisesRegex(ServiceError, "matched_renewal_cutover_required"):
            self.operation.run()
        self.assertEqual(self.policy.read()["intent"], "maintenance")


class RecoveryImageTests(unittest.TestCase):
    def setUp(self):
        self.name = "mail-admin-recovery-a599616f-cefc-4277-a2c3-931d2032ebfa"
        self.identifier = "c" * 64
        self.docker = MagicMock(executable="/usr/bin/docker")
        self.deployment = {
            "dashboard_image_id": "sha256:" + "a" * 64,
            "inputs": {"service_uid": 10001, "service_gid": 10001},
            "consumers": {"mail-admin": {"environment": {}}},
            "transient_resources": {
                "memory_limit_bytes": 128 * 1024 * 1024,
                "tmpfs_limit_bytes": 8 * 1024 * 1024,
                "cpu_limit": 0.5,
            },
        }
        self.observed = {
            "Id": self.identifier,
            "Name": "/" + self.name,
            "Image": self.deployment["dashboard_image_id"],
            "Config": {"Labels": {"mail-admin.recovery-run": self.name}},
            "State": {"Running": False, "Status": "exited", "ExitCode": 0},
        }
        self.docker.inspect.return_value = self.observed
        self.docker.run.return_value = self.identifier
        self.tools = ImageTools(self.docker, self.deployment)

    def test_resume_needs_matching_container_exit_and_a_current_database_probe(self):
        self.tools.verify_recovery(self.name)
        self.assertIn("probe", self.docker.run.call_args.args[0])
        for state in (
            {"Running": True, "Status": "running", "ExitCode": 0},
            {"Running": False, "Status": "exited", "ExitCode": 9},
            {"Running": False, "Status": "created", "ExitCode": 0},
        ):
            self.observed["State"] = state
            self.docker.run.reset_mock()
            with self.assertRaisesRegex(ServiceError, "recovery_completion_unknown"):
                self.tools.verify_recovery(self.name)
            self.assertEqual(self.docker.run.call_count, 1)

    def test_changed_image_or_foreign_container_label_cannot_authorize_resume(self):
        self.observed["Image"] = "sha256:" + "b" * 64
        with self.assertRaisesRegex(ServiceError, "recovery_completion_unknown"):
            self.tools.verify_recovery(self.name)
        self.observed["Config"]["Labels"] = {}
        with self.assertRaisesRegex(ServiceError, "untrusted_operation_container"):
            self.tools.verify_recovery(self.name)

    def test_private_interactive_container_is_named_retained_and_attached_without_capturing_secrets(
        self,
    ):
        self.docker.run.side_effect = ["", self.identifier, self.identifier, ""]
        with (
            patch("host.image_tools.sys.stdin.isatty", return_value=True),
            patch("host.image_tools.sys.stdout.isatty", return_value=True),
            patch(
                "host.image_tools.subprocess.run", return_value=SimpleNamespace(returncode=0)
            ) as attached,
        ):
            self.tools.offline_utility(self.name)
        created = self.docker.run.call_args_list[1].args[0]
        self.assertEqual(created[:4], ("create", "-it", "--name", self.name))
        self.assertNotIn("--rm", created)
        self.assertIn("none", created)
        self.assertIn("FOWNER", created)
        self.assertEqual(created[created.index("--log-driver") + 1], "none")
        self.assertNotIn("--log-opt", created)
        attached.assert_called_once_with(
            ("/usr/bin/docker", "start", "--attach", "--interactive", self.name),
            check=False,
        )

    def test_nonterminal_recovery_performs_no_docker_action(self):
        with patch("host.image_tools.sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(ServiceError, "private_terminal_required"):
                self.tools.offline_utility(self.name)
        self.docker.run.assert_not_called()
