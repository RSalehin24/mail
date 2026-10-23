import hashlib
import os
import sqlite3
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from helper.maddy import LAUNCHER
from host.cli import cutover_action
from host.cutover import Cutover
from host.cutover_host import RootCutoverHost
from host.locks import LockPolicy
from host.release_files import ReleaseFiles, RollbackPackage
from shared.errors import ServiceError
from shared.files import protected_json
from tests.support import StateFixture
from tests.unit.test_host_capture_alerts import SnapshotFixture


class ImageFixture:
    def __init__(self):
        self.commands = []
        self.architecture = "amd64"

    def inspect(self, identity):
        return {"Id": identity, "Architecture": self.architecture, "Os": "linux"}

    def run(self, arguments):
        self.commands.append(arguments)
        if arguments[:2] == ("image", "save"):
            Path(arguments[3]).write_bytes(b"synthetic immutable image archive")
        return ""


class ReleaseFixture(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve()
        self.plan = {}
        for name, mode in (("renewal_service", 0o644), ("renewal_script", 0o750)):
            target, source = self.root / (name + ".old"), self.root / (name + ".new")
            target.write_bytes((name + " original").encode())
            source.write_bytes((name + " candidate").encode())
            target.chmod(mode)
            source.chmod(0o600)
            self.plan[name] = {
                "source": str(source),
                "target": str(target),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "mode": mode,
            }
        self.files = ReleaseFiles(
            self.plan, (os.getuid(), tuple(Path(r["target"]) for r in self.plan.values()))
        )
        self.images = ImageFixture()
        self.package = RollbackPackage(self.root / "rollback", self.files, self.images)
        self.release = {
            "artifact_set_sha256": "a" * 64,
            "schema_version": 1,
            "helper_protocol": 1,
            "tls_layout": "legacy",
            "consumers": {"maddy": {"image_id": "sha256:" + "b" * 64}},
        }

    def old_files(self):
        for name, record in self.plan.items():
            self.assertEqual(Path(record["target"]).read_bytes(), (name + " original").encode())


class RollbackPackageTests(ReleaseFixture):
    def test_package_restores_exact_files_modes_and_images_without_touching_newer_mail(self):
        identity = self.package.create(self.release)
        self.files.candidate()
        newer = self.root / "newer-message"
        newer.write_bytes(b"mail delivered after the rollback snapshot")
        restored = self.package.restore(identity)
        self.assertEqual(restored, self.release)
        self.old_files()
        for record in self.plan.values():
            self.assertEqual(Path(record["target"]).stat().st_mode & 0o777, record["mode"])
        self.assertEqual(newer.read_bytes(), b"mail delivered after the rollback snapshot")
        self.assertEqual(self.images.commands[-1][:2], ("image", "load"))
        self.assertEqual((self.package.directory / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_corrupt_original_file_stops_rollback_before_image_load_or_any_file_write(self):
        identity = self.package.create(self.release)
        self.files.candidate()
        (self.package.directory / "renewal_script").write_bytes(b"tampered")
        before = {name: Path(record["target"]).read_bytes() for name, record in self.plan.items()}
        commands = list(self.images.commands)
        with self.assertRaisesRegex(ServiceError, "rollback_file_digest_mismatch"):
            self.package.restore(identity)
        self.assertEqual(self.images.commands, commands)
        self.assertEqual(
            {name: Path(record["target"]).read_bytes() for name, record in self.plan.items()},
            before,
        )

    def test_corrupt_image_or_manifest_cannot_start_rollback(self):
        identity = self.package.create(self.release)
        image = self.package.directory / "images.tar"
        original = image.read_bytes()
        image.write_bytes(b"tampered")
        with self.assertRaisesRegex(ServiceError, "rollback_image_archive_mismatch"):
            self.package.restore(identity)
        image.write_bytes(original)
        with self.assertRaisesRegex(ServiceError, "rollback_manifest_digest_mismatch"):
            self.package.restore("c" * 64)
        self.old_files()

    def test_changed_candidate_source_is_rejected_before_replacing_any_host_file(self):
        Path(self.plan["renewal_script"]["source"]).write_bytes(b"changed after qualification")
        with self.assertRaisesRegex(ServiceError, "release_source_digest_mismatch"):
            self.files.candidate()
        self.old_files()

    def test_unsafe_later_file_mode_is_rejected_before_replacing_the_first_file(self):
        self.plan["renewal_script"]["mode"] = 0o777
        with self.assertRaisesRegex(ServiceError, "unsafe_release_file_mode"):
            self.files.candidate()
        self.old_files()

    def test_unsafe_source_modes_or_symlinks_never_become_installed_code(self):
        source = Path(self.plan["renewal_script"]["source"])
        source.chmod(0o666)
        with self.assertRaisesRegex(ServiceError, "untrusted_release_file"):
            self.files.candidate()
        source.chmod(0o600)
        moved = source.with_suffix(".preserved")
        source.rename(moved)
        source.symlink_to(moved)
        with self.assertRaisesRegex(ServiceError, "release_file_symlink"):
            self.files.candidate()
        self.old_files()

    def test_wrong_image_architecture_prevents_a_completed_rollback_manifest(self):
        self.images.architecture = "arm64"
        with self.assertRaisesRegex(ServiceError, "rollback_image_mismatch"):
            self.package.create(self.release)
        self.assertFalse((self.package.directory / "manifest.json").exists())
        self.assertEqual(self.images.commands, [])
        self.old_files()

    def test_target_set_cannot_be_changed_by_a_release_plan(self):
        with self.assertRaisesRegex(ServiceError, "release_file_targets_mismatch"):
            ReleaseFiles(self.plan, (os.getuid(), (self.root / "unrelated",)))

    def test_image_archive_symlink_or_open_package_permissions_block_restore(self):
        identity = self.package.create(self.release)
        archive = self.package.directory / "images.tar"
        preserved = self.package.directory / "preserved.tar"
        archive.rename(preserved)
        archive.symlink_to(preserved)
        with self.assertRaisesRegex(ServiceError, "untrusted_rollback_image_archive"):
            self.package.restore(identity)
        self.package.directory.chmod(0o755)
        with self.assertRaisesRegex(ServiceError, "untrusted_rollback_directory"):
            self.package.restore(identity)
        self.old_files()


class CutoverHostFixture:
    def __init__(self, scope, fixture):
        self.scope, self.fixture = scope, fixture
        self.events = []
        self.timer_enabled = False
        self.timer_active = False
        self.layout = "legacy"
        self.fail = None

    def event(self, name, locked):
        assert self.scope.locked is locked
        self.events.append(name)
        if self.fail == name:
            self.fail = None
            raise ServiceError("synthetic_" + name + "_failure", 503)

    def schedule(self):
        return {"enabled": True, "active": True}

    def pause(self):
        self.timer_enabled = self.timer_active = False
        self.event("pause", False)

    def snapshot(self):
        self.event("snapshot", True)
        return self.fixture.release

    def compatible(self, release):
        self.event("compatible", True)

    def forward(self):
        self.layout = "generations"
        self.event("forward", True)

    def verify_service(self):
        self.event("verify_service", False)

    def restore(self, release):
        self.layout = release["tls_layout"]
        self.event("restore", True)

    def verify_release(self, release):
        self.event("verify_release", False)
        self.fixture.old_files()
        assert self.layout == release["tls_layout"]

    def resume(self, schedule):
        self.timer_enabled, self.timer_active = schedule["enabled"], schedule["active"]
        self.event("resume", False)


class CutoverTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.scope = SnapshotFixture()
        self.scope.locks = SimpleNamespace(policy=LockPolicy(0.03, os.getuid()))
        self.outcome = self.root / "cutover.json"
        self.outcome.with_suffix(".lock").touch(mode=0o600)
        self.host = CutoverHostFixture(self.scope, self)
        self.operation = Cutover(self.scope, (self.host, self.package, self.outcome))

    def phase(self):
        return protected_json(self.outcome, os.getuid())["phase"]

    def test_service_checks_and_schedule_resume_run_after_both_host_locks_are_released(self):
        self.operation.run()
        self.assertEqual(self.phase(), "complete")
        self.assertTrue(self.host.timer_enabled and self.host.timer_active)
        self.assertLess(self.host.events.index("verify_service"), self.host.events.index("resume"))
        self.assertEqual(self.host.layout, "generations")
        with self.assertRaisesRegex(ServiceError, "cutover_already_recorded"):
            self.operation.run()

    def test_failed_service_verification_stays_paused_until_explicit_matched_rollback(self):
        self.host.fail = "verify_service"
        with self.assertRaises(ServiceError):
            self.operation.run()
        self.assertEqual(self.phase(), "rollback_required")
        self.assertFalse(self.host.timer_enabled or self.host.timer_active)
        self.operation.rollback()
        self.assertEqual(self.phase(), "rolled_back")
        self.assertEqual(self.host.layout, "legacy")
        self.assertLess(self.host.events.index("verify_release"), self.host.events.index("resume"))
        self.old_files()

    def test_failure_after_timer_enable_pauses_it_again_before_reporting_failure(self):
        self.host.fail = "resume"
        with self.assertRaises(ServiceError):
            self.operation.run()
        self.assertFalse(self.host.timer_enabled or self.host.timer_active)
        self.assertEqual(self.phase(), "rollback_required")
        self.operation.rollback()
        self.assertEqual(self.phase(), "rolled_back")

    def test_interrupted_install_keeps_a_verified_package_for_explicit_rollback(self):
        original = self.files.replace
        calls = []

        def interrupted(target, content):
            original(target, content)
            calls.append(target)
            if len(calls) == 1:
                raise OSError("synthetic host-file replacement interruption")

        with patch.object(self.files, "replace", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.operation.run()
        self.assertFalse(self.host.timer_enabled)
        self.operation.rollback()
        self.old_files()
        self.assertEqual(self.phase(), "rolled_back")

    def test_corrupt_package_or_failed_served_verification_never_resumes_the_legacy_timer(self):
        self.operation.run()
        (self.package.directory / "renewal_script").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ServiceError, "rollback_file_digest_mismatch"):
            self.operation.rollback()
        self.assertFalse(self.host.timer_enabled or self.host.timer_active)
        self.assertNotEqual(self.phase(), "rolled_back")

    def test_failed_rollback_tls_verification_keeps_the_restored_legacy_timer_disabled(self):
        self.operation.run()
        self.host.fail = "verify_release"
        with self.assertRaises(ServiceError):
            self.operation.rollback()
        self.assertFalse(self.host.timer_enabled or self.host.timer_active)
        self.assertNotEqual(self.phase(), "rolled_back")
        self.old_files()

    def test_private_lease_prevents_simultaneous_forward_and_rollback_commands(self):
        create = self.package.create

        def packaging(release):
            with self.assertRaisesRegex(ServiceError, "maintenance_busy"):
                self.operation.rollback()
            return create(release)

        with patch.object(self.package, "create", side_effect=packaging):
            self.operation.run()

    def test_rollback_never_runs_an_older_image_against_a_newer_or_corrupt_schema(self):
        path = self.root / "different-schema.db"
        with sqlite3.connect(path) as connection:
            connection.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(ServiceError, "incompatible_rollback_schema"):
            RootCutoverHost._schema(path, 1)
        path.write_bytes(b"corrupt SQLite database")
        with self.assertRaisesRegex(ServiceError, "rollback_schema_unavailable"):
            RootCutoverHost._schema(path, 1)


class CutoverAdapterTests(ReleaseFixture):
    def setUp(self):
        super().setUp()
        self.host = RootCutoverHost.__new__(RootCutoverHost)
        self.host.files = self.files
        self.host.systemd = MagicMock()
        self.host.context = SimpleNamespace(generations=MagicMock(), docker=MagicMock())

    def test_rollback_support_requires_the_launcher_that_the_real_helper_actually_executes(self):
        targets = {'renewal_service': '/etc/systemd/system/maddy-cert-renew.service',
                   'renewal_timer': '/etc/systemd/system/maddy-cert-renew.timer',
                   'renewal_script': '/opt/maddy/renew-cert.sh',
                   'maddy_compose': '/opt/maddy/compose.yml'}
        self.host.files = SimpleNamespace(plan={role: {'target': value}
                                               for role, value in targets.items()})
        self.host.context.deployment = {
            'consumers': {'maddy': {'compose_file': targets['maddy_compose']}},
        }
        paths = ['/usr/local/lib/mail-admin/helper/service.py',
                 '/usr/local/lib/mail-admin/host/cli.py',
                 '/etc/systemd/system/mail-admin-helper.service', LAUNCHER]
        self.host.configuration = {'support_files': paths}
        with patch('host.cutover_host.Path.rglob', return_value=[]):
            self.host._validate_plan()
            self.host.configuration['support_files'] = paths[:-1]
            with self.assertRaisesRegex(ServiceError, 'rollback_support_files_incomplete'):
                self.host._validate_plan()

    def test_maddy_only_rollback_pauses_a_later_dashboard_and_keeps_its_generation_and_data(self):
        generations = self.host.context.generations
        generations.read.return_value = {
            "consumers": {
                "mail-admin": {
                    "intent": "expected_running",
                    "active_generation": "new",
                    "served_expiry": 1800000100,
                }
            },
            "acme_run": {"state": "completed", "exit_code": 0},
        }
        generations.referenced_generations.return_value = {"new"}
        release = {
            **self.release,
            "certificate_state": {
                "coordination_migrated": False,
                "consumers": {
                    "maddy": {"intent": "expected_running"},
                    "mail-admin": {"intent": "never_deployed"},
                },
            },
            "consumers": {"maddy": {"tls_directory": str(self.root)}},
        }
        events = []
        generations.write.side_effect = lambda _: events.append("publish_matched_state")
        with (
            patch.object(
                self.host, "_pause_new_dashboard", side_effect=lambda: events.append("pause_web")
            ),
            patch("host.cutover_host.DockerConsumer"),
        ):
            self.host.restore(release)
        self.assertEqual(events, ["pause_web", "publish_matched_state"])
        state = generations.write.call_args.args[0]
        dashboard = state["consumers"]["mail-admin"]
        self.assertEqual(dashboard["intent"], "maintenance")
        self.assertEqual(dashboard["reason"], "rollback_to_maddy_only")
        self.assertEqual(dashboard["active_generation"], "new")
        self.assertIn("new", state["rollback_generations"])
        self.assertEqual(state["acme_run"], {"state": "completed", "exit_code": 0})

    def test_running_later_dashboard_blocks_verified_rollback(self):
        self.host.context.generations.read.return_value = {
            "consumers": {
                "mail-admin": {"intent": "maintenance", "reason": "rollback_to_maddy_only"}
            }
        }
        self.host.context.docker.inspect.return_value = {"State": {"Running": True}}
        with patch.object(self.host, "compatible"):
            with self.assertRaisesRegex(ServiceError, "rollback_dashboard_not_paused"):
                self.host.verify_release(self.release)

    def test_active_consumer_maintenance_blocks_a_cutover_snapshot(self):
        self.host.context.generations.read.return_value = {
            "consumers": {"mail-admin": {"intent": "maintenance", "reason": "offline_recovery"}}
        }
        with self.assertRaisesRegex(ServiceError, "consumer_maintenance_active"):
            self.host.snapshot()
        self.host.context.docker.inspect.assert_not_called()

    def test_gate_or_approval_failure_prevents_all_cutover_host_actions(self):
        for failing in ("require_approval", "require_checkpoint", "qualify_preflight"):
            with self.subTest(failing=failing):
                self._blocked_gate(failing)

    def _blocked_gate(self, failing):
        with (
            patch("host.cli.require_approval"),
            patch("host.cli.require_checkpoint"),
            patch("host.cli.qualify_preflight"),
            patch("host.cli.RootCutoverHost") as host,
            patch("host.cli." + failing, side_effect=ServiceError("synthetic_gate_failed", 409)),
        ):
            with self.assertRaises(ServiceError):
                cutover_action("tls-cutover", MagicMock())
            host.assert_not_called()
