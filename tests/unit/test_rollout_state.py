import copy
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from host.locks import HostLock, LockPolicy
from host.rollout import (
    RolloutState,
    operational_guard,
    rollout_directory,
    rollout_operation,
)
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.rollout_support import write_gate_fixtures
from tests.support import StateFixture


class RolloutStateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.clock_patch = patch("host.rollout.time.time", return_value=self.fixture.now)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.root = self.fixture.root.resolve()
        self.deployment = {
            "artifact_set_sha256": "a" * 64, "specification_sha256": "b" * 64,
            "qualification_suite_sha256": "c" * 64,
            "inputs": {"lock_wait_seconds": 0.03, "target_instance_id": "fixture-target",
                       "target_ssh_host_key_sha256": "d" * 64},
        }
        self.next = {**self.deployment, "artifact_set_sha256": "e" * 64}
        self.original = RolloutState(self.deployment, (self.root, os.getuid()))
        self.following = RolloutState(self.next, (self.root, os.getuid()))

    def completed(self, phase="complete"):
        self.original.select()
        directory = self.root / self.deployment["artifact_set_sha256"]
        directory.mkdir(mode=0o700)
        atomic_json(directory / "legacy.json", {"state": "legacy_drained"})
        atomic_json(directory / "cutover.json", {"phase": phase})
        (directory / "rollback-set").mkdir(mode=0o700)
        if phase == "complete":
            self.gate_fixtures(directory)
        return directory

    def gate_fixtures(self, directory):
        write_gate_fixtures(directory, self.deployment, self.fixture.now)

    def assert_releases_are_isolated(self, phase):
        directory = self.completed(phase)
        before = {str(p.relative_to(directory)): p.read_bytes()
                  for p in directory.rglob("*") if p.is_file()}
        self.following.select()
        self.following.require_selected()
        self.assertEqual(before, {str(p.relative_to(directory)): p.read_bytes()
                                 for p in directory.rglob("*") if p.is_file()})
        with self.assertRaisesRegex(ServiceError, "another_rollout_selected"):
            self.original.require_selected()

    def test_completed_release_allows_a_new_namespace_without_changing_old_records(self):
        self.assert_releases_are_isolated("complete")

    def test_rolled_back_release_allows_a_new_namespace_without_changing_old_records(self):
        self.assert_releases_are_isolated("rolled_back")

    def test_cutover_complete_does_not_release_a_baseline_before_actual_g08_acceptance(self):
        directory = self.completed()
        atomic_json(directory / "checkpoints.json", {})
        with self.assertRaisesRegex(ServiceError, "required_checkpoint_missing"):
            self.following.select()
        self.original.require_selected()

    def test_unresolved_old_phase_or_manual_completion_blocks_another_release(self):
        directory = self.completed("rollback_required")
        phases = ("pausing", "installing", "activating", "verifying_service", "rollback_required")
        for phase in phases:
            with self.subTest(phase=phase):
                atomic_json(directory / "cutover.json", {"phase": phase})
                with self.assertRaisesRegex(ServiceError, "previous_rollout_unresolved"):
                    self.following.select()
                self.original.require_selected()
        atomic_json(directory / "cutover.json", {"phase": "complete"})
        atomic_json(directory / "legacy.json", {"manual_completion": "unknown"})
        with self.assertRaisesRegex(ServiceError, "previous_rollout_unresolved"):
            self.following.select()
        self.original.require_selected()

    def test_missing_completion_or_old_unnamespaced_records_never_become_a_clean_start(self):
        self.original.select()
        with self.assertRaises(FileNotFoundError):
            self.following.select()
        self.original.require_selected()
        atomic_json(self.root / "legacy.json", {"state": "entering"})
        with self.assertRaisesRegex(ServiceError, "unnamespaced_rollout"):
            self.original.select()

    def test_same_release_resumes_its_selection_but_does_not_rewrite_durable_state(self):
        self.original.select()
        before = self.original.path.stat().st_ino, self.original.path.read_bytes()
        self.original.select()
        self.assertEqual(
            before, (self.original.path.stat().st_ino, self.original.path.read_bytes())
        )

    def test_missing_active_pointer_cannot_hide_an_unresolved_release(self):
        previous = self.root / ("f" * 64)
        previous.mkdir(mode=0o700)
        atomic_json(previous / "cutover.json", {"phase": "rollback_required"})
        with self.assertRaisesRegex(ServiceError, "active_rollout_missing"):
            self.following.select()
        self.assertFalse(self.following.path.exists())
        self.assertEqual(protected_json(previous / "cutover.json", os.getuid()), {
            "phase": "rollback_required",
        })

    def test_dangling_active_pointer_cannot_be_replaced_by_fresh_selection(self):
        self.original.path.symlink_to(self.root / "missing-state")
        with self.assertRaisesRegex(ServiceError, "untrusted_active_rollout"):
            self.original.select()
        self.assertTrue(self.original.path.is_symlink())

    def test_target_mismatch_and_foreign_directory_cannot_close_an_old_attempt(self):
        directory = self.completed()
        changed = copy.deepcopy(self.next)
        changed["inputs"]["target_instance_id"] = "another-target"
        with self.assertRaisesRegex(ServiceError, "rollout_target_changed"):
            RolloutState(changed, (self.root, os.getuid())).select()
        directory.chmod(0o755)
        with self.assertRaisesRegex(ServiceError, "untrusted_maintenance_directory"):
            self.following.select()
        self.original.require_selected()

    def test_paths_and_checkpoints_bind_the_release_and_share_one_global_operation_lease(self):
        first, second = rollout_directory(self.deployment), rollout_directory(self.next)
        self.assertNotEqual(first, second)
        self.assertEqual(operational_guard(self.deployment).paths.directory, first)
        self.assertEqual(rollout_operation(self.deployment).path, rollout_operation(self.next).path)
        self.assertEqual(first.name, self.deployment["artifact_set_sha256"])
        invalid = {**self.deployment, "artifact_set_sha256": "../escape"}
        with self.assertRaisesRegex(ServiceError, "qualified_artifact_set_required"):
            rollout_directory(invalid)
        lock = self.root / "operation.lock"
        lock.touch(mode=0o600)
        with HostLock(lock, LockPolicy(0.03, os.getuid())):
            selected = rollout_operation(self.next)
            selected.path, selected.policy = lock, replace(selected.policy, owner=os.getuid())
            with self.assertRaisesRegex(ServiceError, "maintenance_busy"):
                with selected:
                    self.fail("A different release acquired an active rollout lease")

    def test_scheduled_retention_uses_only_the_selected_release_baseline(self):
        from host.backup_operations import BackupOperations

        directory = self.completed()
        old = {"snapshot_id": "old-snapshot"}
        atomic_json(directory / "baseline-manifest.json", old)
        following = self.root / self.next["artifact_set_sha256"]
        following.mkdir(mode=0o700)
        atomic_json(following / "baseline-manifest.json", {"snapshot_id": "new-snapshot"})
        instance = BackupOperations.__new__(BackupOperations)
        instance.deployment = self.next
        with patch("host.backup_operations.rollout_directory", return_value=following), patch(
            "host.backup_operations.protected_json",
            side_effect=lambda p: protected_json(p, os.getuid()),
        ):
            self.assertEqual(instance.baseline_manifest()["snapshot_id"], "new-snapshot")
        self.assertEqual(json.loads((directory / "baseline-manifest.json").read_bytes()), old)

    def test_baseline_retention_requires_the_real_final_contract_gate(self):
        from host.backup_operations import BackupOperations
        from host.rollout import RolloutGuard, RolloutPaths

        directory = self.completed()
        instance = BackupOperations.__new__(BackupOperations)
        instance.deployment = {**self.deployment, "rollback_snapshot_ids": []}
        guard = RolloutGuard(self.deployment, (
            RolloutPaths(directory=directory), os.getuid(), lambda: self.fixture.now,
        ))
        with (
            patch.object(instance, "baseline_manifest", return_value={"snapshot_id": "baseline"}),
            patch("host.backup_operations.operational_guard", return_value=guard),
        ):
            self.assertEqual(instance._protected({"snapshot_id": "current"}), {"current"})
            references = protected_json(directory / "checkpoints.json", os.getuid())
            references.pop("G08")
            atomic_json(directory / "checkpoints.json", references)
            self.assertEqual(instance._protected({"snapshot_id": "current"}), {
                "current", "baseline",
            })
