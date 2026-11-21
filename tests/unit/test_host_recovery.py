import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from host.consumers import ConsumerIntents
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
        self.intents = ConsumerIntents(
            self.fixture.root / "consumers.json", os.getuid(), lambda: self.fixture.now
        )
        self.scope = SnapshotFixture()
        self.scope.locks = SimpleNamespace(policy=LockPolicy(0.03, os.getuid()))
        self.consumer = RecoveryFixture(self.policy, self.scope)
        self.outcome = self.fixture.root / "outcome.json"
        self.outcome.with_suffix(".lock").touch(mode=0o600)
        self.operation = Recovery(self.scope, (self.intents, self.consumer, self.outcome))

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
            self.intents.read()["consumers"]["mail-admin"]["intent"], "maintenance"
        )

    def test_interruption_before_docker_stop_still_blocks_web_startup(self):
        with patch.object(self.intents, "maintenance", side_effect=OSError("synthetic death")):
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
