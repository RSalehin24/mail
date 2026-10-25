import hashlib
import json
import os
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from helper.process import CommandResult
from host.backup import BackupPaths
from host.render import render
from host.repository_backup import RepositoryBackup
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository, ResticSnapshot
from host.snapshot_tree import snapshot_tree
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.restic_support import LocalResticProcess


class ResticTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="mail-admin-restic-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o700)
        executable = self.root / "restic"
        executable.write_bytes(b"synthetic executable identity")
        executable.chmod(0o700)
        password = self.root / "password.key"
        password.write_bytes(b"p" * 64)
        password.chmod(0o600)
        credentials = self.root / "credentials.json"
        atomic_json(credentials, {"access_key_id": "a" * 32, "secret_access_key": "b" * 64})
        jobs, staging = self.root / "jobs", self.root / "staging"
        for directory in (jobs, staging):
            directory.mkdir(mode=0o700)
        (jobs / "command.lock").touch(mode=0o600)
        self.configuration = {
            "inputs": {"backup_destination": "s3://fixture-bucket/mail-admin/"},
            "backup_r2": {
                "endpoint": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
                "credentials_path": str(credentials), "max_archive_bytes": 1024 * 1024,
                "max_bucket_bytes": 16 * 1024 * 1024, "retention_seconds": 7 * 86400,
            },
            "backup_restic": {
                "executable": str(executable), "executable_sha256": hashlib.sha256(
                    executable.read_bytes()
                ).hexdigest(), "password_file": str(password), "state_directory": str(jobs),
                "staging_directory": str(staging), "repository_id": "c" * 64,
                "installation_id": "fixture-installation", "command_timeout_seconds": 30,
            },
        }
        self.process = ResticProcess(self.configuration, os.getuid())
        self.now = 1_800_000_000.0
        self.budget = Mock()
        self.repository = ResticRepository(self.process, lambda: self.now, self.budget)

    def test_actual_command_and_local_repository_share_the_reserved_memory_target(self):
        with patch("host.restic_process.execute", return_value=CommandResult(0, "{}")) as execute:
            self.process.run(("cat", "config"))
        command = execute.call_args.args[0]
        self.configuration["fixture_repository"] = str(self.root / "local-repository")
        local = LocalResticProcess(self.configuration, os.getuid())
        for name, expected in {"GOMEMLIMIT": "96MiB", "GOMAXPROCS": "1", "TZ": "UTC"}.items():
            self.assertEqual(command.environment[name], expected)
            self.assertEqual(local.environment()[name], expected)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", local.environment())

    def test_progress_is_suppressed_without_losing_the_machine_readable_completion_summary(self):
        summary = json.dumps({'message_type': 'summary', 'snapshot_id': 'd' * 64})
        with patch('host.restic_process.execute',
                   return_value=CommandResult(0, summary, 'backend diagnostic\n')) as run:
            self.assertEqual(self.process.run(('backup', '/synthetic-snapshot')), summary)
        arguments = run.call_args.args[0].arguments
        self.assertIn('--quiet', arguments)
        self.assertIn('--json', arguments)
        self.assertEqual(run.call_args.args[0].timeout, 30)
        self.assertFalse(run.call_args.args[0].merge_stderr)

    def test_publisher_uses_the_verified_restic_time_format_and_utc_environment(self):
        snapshot = self.repository.staging / str(uuid.uuid4())
        snapshot.mkdir()
        atomic_json(snapshot / "snapshot.json", {"cutoff": self.now, "accounts": {}})
        (snapshot / "message").write_bytes(b"synthetic message")
        summary = {"message_type": "summary", "snapshot_id": "d" * 64,
                   "data_added_packed": 100}
        with patch.object(self.process, "run", side_effect=[
            json.dumps({"id": "c" * 64, "version": 2}), json.dumps(summary),
        ]) as run:
            self.repository.publish(snapshot)
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[arguments.index("--time") + 1],
                         datetime.fromtimestamp(self.now, UTC).strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(self.process.environment()["TZ"], "UTC")
        self.assertIn("host,tags", arguments)

    def test_interrupted_command_remains_unknown_and_blocks_the_next_invocation(self):
        with patch("host.restic_process.execute", side_effect=ServiceError("command_deadline")):
            with self.assertRaises(ServiceError):
                self.process.run(("check",))
        with patch("host.restic_process.execute") as execute:
            with self.assertRaisesRegex(ServiceError, "backup_command_reconciliation_required"):
                self.process.run(("check",))
        execute.assert_not_called()
        record = protected_json(next(self.process.paths.state.glob("*.json")), os.getuid())
        self.assertEqual(record["state"], "started")

    def test_nonzero_exit_is_recorded_and_completed_cache_is_removed(self):
        with patch("host.restic_process.execute", return_value=CommandResult(3, "not logged")):
            with self.assertRaisesRegex(ServiceError, "restic_exit_3"):
                self.process.run(("backup",))
        record = protected_json(next(self.process.paths.state.glob("*.json")), os.getuid())
        self.assertEqual(record["exit_code"], 3)
        self.assertEqual(record["state"], "exited")
        self.assertFalse(Path(record["cache"]).exists())

    def test_executable_drift_is_rejected_before_credentials_or_process_execution(self):
        self.process.paths.executable.write_bytes(b"changed executable")
        with patch("host.restic_process.execute") as execute:
            with self.assertRaisesRegex(ServiceError, "restic_executable_drift"):
                self.process.run(("check",))
        execute.assert_not_called()

    def test_reconciliation_keeps_the_exit_unknown_and_never_replays_the_original_command(self):
        path = self.process.paths.state / "interrupted.json"
        cache = self.process.paths.state / (str(uuid.uuid4()) + "-cache")
        cache.mkdir(mode=0o700)
        atomic_json(path, {"state": "started", "cache": str(cache)})
        with patch("host.restic_process.require_no_active_commands") as quiet, patch.object(
            self.process, "_execute", side_effect=[json.dumps({"id": "c" * 64}), "", ""]
        ) as execute:
            self.process.reconcile()
        quiet.assert_called_once_with({str(cache)})
        self.assertEqual([call.args[0] for call in execute.call_args_list],
                         [("cat", "config"), ("unlock",), ("check", "--read-data")])
        record = protected_json(path, os.getuid())
        self.assertIsNone(record["exit_code"])
        self.assertEqual(record["state"], "reconciled")
        self.assertFalse(cache.exists())
        self.process.require_quiet()

    def test_reconciliation_refuses_survivors_or_failed_repository_checks(self):
        path = self.process.paths.state / "interrupted.json"
        atomic_json(path, {"state": "started", "cache": "synthetic-cache"})
        with patch("host.restic_process.require_no_active_commands",
                   side_effect=ServiceError("backup_command_still_running")), patch.object(
            self.process, "_execute"
        ) as execute:
            with self.assertRaisesRegex(ServiceError, "backup_command_still_running"):
                self.process.reconcile()
            execute.assert_not_called()
        with patch("host.restic_process.require_no_active_commands"), patch.object(
            self.process, "_execute", side_effect=[json.dumps({"id": "c" * 64}), "",
                                                   ServiceError("repository_failed")]
        ):
            with self.assertRaisesRegex(ServiceError, "repository_failed"):
                self.process.reconcile()
        self.assertEqual(protected_json(path, os.getuid())["state"], "started")

    def test_retention_uses_the_current_clock_and_preserves_protected_shared_history(self):
        old, boundary, recent, current = [self.point(age) for age in (9, 7, 6, 0)]
        with patch.object(self.repository, "snapshots", return_value=[
            old, boundary, recent, current,
        ]), patch.object(self.process, "run", return_value="") as run:
            expired = self.repository.prune({old.identifier, current.identifier})
        self.assertEqual(expired, [boundary.identifier])
        self.assertEqual(run.call_args_list[0].args[0], ("check", "--read-data"))
        self.assertEqual(run.call_args_list[1].args[0], ("forget", boundary.identifier))
        self.assertEqual(run.call_args_list[-1].args[0], ("check", "--read-data"))

    def test_failed_check_cannot_forget_or_prune_any_snapshot(self):
        old, current = self.point(8), self.point(0)
        with patch.object(self.repository, "snapshots", return_value=[old, current]), patch.object(
            self.process, "run", side_effect=ServiceError("failed_check")
        ) as run:
            with self.assertRaisesRegex(ServiceError, "failed_check"):
                self.repository.prune({current.identifier})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ("check", "--read-data"))

    def test_pruning_retries_garbage_collection_when_a_previous_forget_already_completed(self):
        current = self.point(0)
        with patch.object(self.repository, "snapshots", return_value=[current]), patch.object(
            self.process, "run", return_value=""
        ) as run:
            self.assertEqual(self.repository.prune({current.identifier}), [])
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ("check", "--read-data"), ("prune", "--max-repack-size", "8M", "--max-unused", "0"),
            ("check", "--read-data"),
        ])

    def test_failed_new_publication_verification_preserves_the_previous_trusted_manifest(self):
        manifest = self.root / "manifest.json"
        previous = {"verified": True, "reference": "previous-point", "recovery_cutoff": 100}
        atomic_json(manifest, previous)
        capture = Mock(paths=BackupPaths(self.repository.staging, manifest, {}))
        backup = RepositoryBackup(capture, self.repository)
        with patch.object(self.repository, "publish", return_value={"reference": "new"}), \
                patch.object(self.repository, "verify", side_effect=ServiceError("verify_failed")):
            with self.assertRaisesRegex(ServiceError, "verify_failed"):
                backup.transfer(self.repository.staging / str(uuid.uuid4()))
        self.assertEqual(protected_json(manifest, os.getuid()), previous)

    def test_future_or_unprotected_repository_cannot_authorize_retention(self):
        point = self.point(-1)
        for protected in (set(), {"e" * 64}, {point.identifier}):
            with patch.object(self.repository, "snapshots", return_value=[point]), patch.object(
                self.process, "run"
            ) as run:
                with self.assertRaises(ServiceError):
                    self.repository.prune(protected)
                run.assert_not_called()

    def test_foreign_repository_and_existing_restore_target_are_refused(self):
        with self.assertRaisesRegex(ServiceError, "untrusted_archive_reference"):
            self.repository.restore("restic:" + "e" * 64 + ":" + "f" * 64, self.root / "new")
        point = self.point(0)
        with patch.object(self.repository, "snapshots", return_value=[point]):
            with self.assertRaisesRegex(ServiceError, "restore_target_exists"):
                self.repository.restore(self.repository.reference(point.identifier), self.root)

    def test_tree_integrity_detects_bytes_permissions_and_escaping_links(self):
        target = self.root / "tree"
        target.mkdir()
        path = target / "message"
        path.write_bytes(b"original")
        original = snapshot_tree(target).sha256
        path.chmod(0o600)
        protected = snapshot_tree(target).sha256
        self.assertNotEqual(original, protected)
        path.write_bytes(b"changed")
        self.assertNotEqual(protected, snapshot_tree(target).sha256)
        (target / "escape").symlink_to(self.root / "password.key")
        with self.assertRaisesRegex(ServiceError, "backup_symlink_escapes_snapshot"):
            snapshot_tree(target)

    def test_shared_writable_restore_parent_is_refused_before_starting_restoration(self):
        point = self.point(0)
        parent = self.root / "public"
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        with patch.object(self.repository, "snapshots", return_value=[point]), patch.object(
            self.process, "run"
        ) as run:
            with self.assertRaisesRegex(ServiceError, "unsafe_restore_parent"):
                self.repository.restore(self.repository.reference(point.identifier), parent / "new")
        run.assert_not_called()
        self.assertFalse((parent / "new").exists())

    def test_tree_entry_bound_is_enforced_before_collecting_an_unbounded_listing(self):
        for name in ("one", "two", "three"):
            (self.root / name).touch()
        with patch("host.snapshot_tree.MAX_ENTRIES", 2):
            with self.assertRaisesRegex(ServiceError, "backup_entry_limit_exceeded"):
                snapshot_tree(self.root)

    def test_completed_command_cleanup_preserves_older_unresolved_outcomes(self):
        for name, state in (("unknown", "started"), ("first", "exited"), ("second", "exited")):
            atomic_json(self.process.paths.state / (name + ".json"), {"state": state})
        with patch("host.restic_process.MAX_COMPLETED_COMMANDS", 1):
            self.process._prune_completed()
        self.assertTrue((self.process.paths.state / "unknown.json").exists())
        self.assertEqual(len(list(self.process.paths.state.glob("*.json"))), 2)

    def test_rendered_backup_timer_and_whole_job_resource_bounds_are_explicit(self):
        configuration = {
            "inputs": {"service_gid": 10001, "backup_schedule": "*-*-* 03:00:00 Asia/Dhaka"},
            "backup_resources": {"memory_limit_bytes": 192 * 1024 * 1024,
                                 "cpu_quota_percent": 50},
        }
        rendered = self.root / "rendered"
        render(configuration, rendered)
        self.assertIn("03:00:00 Asia/Dhaka", (rendered / "mail-admin-backup.timer").read_text())
        service = (rendered / "mail-admin-backup.service").read_text()
        self.assertIn("MemoryMax=201326592", service)
        self.assertIn("CPUQuota=50%", service)
        configuration["backup_resources"]["memory_limit_bytes"] = None
        with self.assertRaisesRegex(ServiceError, "backup_resource_limits_required"):
            render(configuration, self.root / "missing-resource-output")
        self.assertFalse((self.root / "missing-resource-output").exists())

    def point(self, age):
        return ResticSnapshot(uuid.uuid4().hex * 2,
                              self.repository.staging / str(uuid.uuid4()),
                              self.now - age * 86400)
