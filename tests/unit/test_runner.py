import gc
import io
import json
import os
import subprocess
import unittest
import uuid
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.maddy import test_restic_restore
from tests.maddy_support import RealMaddyFixture
from tests.runtime_support import RuntimeFixture
from tests.support import StateFixture
from tools.resources import MADDY_MEMORY_MIB, WEB_MEMORY_MIB, fixture_budget
from tools.run_linux import pinned_runtime_image, suite_command
from tools.run_tests import ROOT, RecordingResult, source_digest


class RunnerTests(unittest.TestCase):
    def test_fixture_observation_cannot_retarget_a_production_name_or_unpinned_image(self):
        state = StateFixture()
        self.addCleanup(state.close)
        fixture = RealMaddyFixture.__new__(RealMaddyFixture)
        fixture.root, fixture.docker = state.root, '/usr/bin/docker'
        fixture.container = 'mail-admin-fixture-' + str(uuid.uuid4())
        fixture.maddy_image = 'sha256:' + 'a' * 64
        wrapper = Path(fixture.helper_docker())
        original = wrapper.read_bytes()
        self.assertIn(fixture.container.encode(), original)
        for name, image in (('maddy', fixture.maddy_image),
                            ('mail-admin', fixture.maddy_image),
                            (fixture.container, 'sha256:' + 'x' * 64),
                            (fixture.container, 'mutable:tag')):
            with self.subTest(container=name), self.assertRaises(RuntimeError):
                fixture.helper_docker((name, image))
            self.assertEqual(wrapper.read_bytes(), original)

    def test_mail_bind_targets_are_private_before_docker_can_create_placeholders(self):
        with (patch.object(RealMaddyFixture, '_local_engine'),
              patch.object(RealMaddyFixture, 'command', return_value=SimpleNamespace(stdout='')),
              patch('tests.maddy_support.shutil.which', return_value='/usr/bin/docker')):
            fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        for path, mode in ((fixture.data, 0o700), (fixture.data / 'tls', 0o700),
                           (fixture.data / 'maddy.conf', 0o600), (fixture.configuration, 0o600)):
            self.assertEqual(path.stat().st_mode & 0o777, mode)
        self.assertEqual((fixture.data / 'maddy.conf').read_bytes(), b'')

    def test_partial_maddy_modules_require_existing_names_and_keep_distinct_reports(self):
        from tools.run_linux import suite_command

        arguments = suite_command('maddy', None, 'test_permissions_sessions')
        self.assertEqual(arguments[:5], ('python', 'tools/run_tests.py', 'maddy',
                                         '--module', 'test_permissions_sessions'))
        self.assertEqual(arguments[5], '--report-directory')
        self.assertTrue(arguments[6].startswith('build/maddy-partial-'))
        self.assertTrue(arguments[6].endswith('/test_permissions_sessions'))
        with self.assertRaises(ValueError):
            suite_command('maddy', Path('build/fixture-evidence.json'), 'test_permissions_sessions')
        for suite, module in (('unit', 'test_permissions_sessions'),
                              ('maddy', '../test_permissions_sessions'),
                              ('maddy', 'test_nonexistent')):
            with self.subTest(suite=suite, module=module), self.assertRaises(ValueError):
                suite_command(suite, None, module)

    def test_restored_web_stops_before_the_original_mail_writer_can_resume(self):
        for error in (None, RuntimeError("synthetic restore failure")):
            with self.subTest(error=error):
                self.assert_restored_cleanup(error)

    def assert_restored_cleanup(self, error):
        case = test_restic_restore.NativeResticRestoreTests()
        events = []

        def verify(web, contexts):
            events.append("verify")
            self.assertEqual(contexts, ("operation", ("restored", "dashboard")))
            if error is not None:
                raise error

        with (
            patch.object(test_restic_restore, "RestoredRuntimeFixture") as fixture,
            patch.object(case, "verify_recovered_https", side_effect=verify),
        ):
            fixture.return_value.close.side_effect = lambda: events.append("close")
            if error is None:
                case.verify_restored_https("operation", ("restored", "dashboard"))
            else:
                with self.assertRaisesRegex(RuntimeError, "synthetic restore failure"):
                    case.verify_restored_https("operation", ("restored", "dashboard"))
            events.append("resume original writer")
            fixture.return_value.close.assert_called_once_with()
        self.assertEqual(events, ["verify", "close", "resume original writer"])

    def test_runtime_stopping_before_port_publication_reports_its_failure(self):
        fixture = RuntimeFixture.__new__(RuntimeFixture)
        fixture.identifier = "fixture-stopped"
        stopped = {"State": {"Running": False}, "NetworkSettings": {"Ports": {}}}
        with (
            patch.object(fixture, "command", return_value=subprocess.CompletedProcess(
                (), 0, json.dumps([stopped]), ""
            )),
            patch.object(fixture, "diagnostics", return_value="safe stopped diagnostics"),
            patch.object(fixture, "api") as api,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "container stopped.*safe stopped diagnostics"
            ):
                fixture.ready()
        api.assert_not_called()

    def test_runtime_waits_for_port_publication_within_the_existing_deadline(self):
        fixture = RuntimeFixture.__new__(RuntimeFixture)
        fixture.identifier = "fixture-starting"
        waiting = {"State": {"Running": True}, "NetworkSettings": {"Ports": {}}}
        ready = {"State": {"Running": True}, "NetworkSettings": {"Ports": {
            "8443/tcp": [{"HostPort": "32123"}],
        }}}
        responses = [subprocess.CompletedProcess((), 0, json.dumps([value]), "")
                     for value in (waiting, ready)]
        with (
            patch.object(fixture, "command", side_effect=responses),
            patch.object(fixture, "api", return_value=(200, {}, {})),
            patch("tests.runtime_support.time.sleep") as delay,
            patch("tests.runtime_support.time.monotonic", side_effect=[0, 0, 0.25]),
        ):
            fixture.ready()
        self.assertEqual(fixture.port, 32123)
        delay.assert_called_once_with(0.25)

    def test_missing_runtime_port_cannot_extend_the_fifteen_second_deadline(self):
        fixture = RuntimeFixture.__new__(RuntimeFixture)
        fixture.identifier = "fixture-unpublished"
        waiting = {"State": {"Running": True}, "NetworkSettings": {"Ports": None}}
        with (
            patch.object(fixture, "command", return_value=subprocess.CompletedProcess(
                (), 0, json.dumps([waiting]), ""
            )),
            patch.object(fixture, "diagnostics", return_value="safe unavailable port"),
            patch("tests.runtime_support.time.sleep"),
            patch("tests.runtime_support.time.monotonic", side_effect=[0, 0, 16]),
        ):
            with self.assertRaisesRegex(RuntimeError, "port not published"):
                fixture.ready()

    def test_cyclic_fixture_objects_are_reclaimed_before_the_next_bounded_test(self):
        class CyclicFixture:
            pass

        enabled = gc.isenabled()
        gc.disable()
        try:
            previous = CyclicFixture()
            previous.reference = previous
            observed = weakref.ref(previous)
            del previous
            self.assertIsNotNone(observed())
            result = RecordingResult(io.StringIO(), True, 0)
            result.startTest(unittest.FunctionTestCase(lambda: None))
            self.assertIsNone(observed())
        finally:
            if enabled:
                gc.enable()

    def test_progress_persists_a_started_test_without_inventing_its_completion(self):
        fixture = StateFixture()
        self.addCleanup(fixture.close)
        progress = fixture.root / "progress.jsonl"
        progress.touch(mode=0o600)
        result = RecordingResult(io.StringIO(), True, 0, progress=progress)
        case = unittest.FunctionTestCase(lambda: None)
        result.startTest(case)
        records = [json.loads(line) for line in progress.read_text().splitlines()]
        self.assertEqual([row["outcome"] for row in records], ["started"])
        self.assertEqual(result.records, {})
        result.addSuccess(case)
        result.stopTest(case)
        records = [json.loads(line) for line in progress.read_text().splitlines()]
        self.assertEqual([row["outcome"] for row in records], ["started", "pass"])

    def test_runtime_fixture_permissions_hold_under_the_private_recovery_umask(self):
        state = StateFixture()
        self.addCleanup(state.close)
        fixture = RuntimeFixture.__new__(RuntimeFixture)
        fixture.directories = {name: state.root / name for name in ("data", "keys", "socket")}
        previous = os.umask(0o077)
        try:
            with patch("tests.runtime_support.os.chown") as ownership:
                fixture.provision_directories()
        finally:
            os.umask(previous)
        self.assertEqual(ownership.call_count, 3)
        for path in fixture.directories.values():
            self.assertEqual(path.stat().st_mode & 0o777, 0o750)
            self.assertIn(unittest.mock.call(path, 0, 10001), ownership.call_args_list)

    def test_runtime_failure_diagnostics_retain_both_container_log_streams(self):
        fixture = RuntimeFixture.__new__(RuntimeFixture)
        fixture.identifier = "fixture-runtime"
        state = {
            "Status": "exited",
            "Running": False,
            "ExitCode": 1,
            "OOMKilled": False,
            "Error": "",
        }
        results = [
            subprocess.CompletedProcess((), 0, json.dumps([{"State": state}]), ""),
            subprocess.CompletedProcess(
                (), 0, "fixture standard output\n", "fixture standard error\n"
            ),
        ]
        with patch.object(fixture, "command", side_effect=results):
            observed = json.loads(fixture.diagnostics())
        self.assertIn("fixture standard output", observed["startup_log"])
        self.assertIn("fixture standard error", observed["startup_log"])

    def test_native_admission_covers_all_runtime_companions_and_keeps_the_host_reserve(self):
        budget = fixture_budget("runtime")
        self.assertEqual(budget.companions_mib, MADDY_MEMORY_MIB + WEB_MEMORY_MIB)
        self.assertEqual(budget.reserve_mib, 128)
        required = (budget.controller_mib + budget.companions_mib + 128) * 1024 * 1024
        self.assertEqual(budget.minimum_available_bytes, required)
        self.assertGreaterEqual(fixture_budget("maddy").companions_mib, 2 * MADDY_MEMORY_MIB)
        self.assertEqual(fixture_budget("maddy").minimum_available_bytes, 544 * 1024**2)
        self.assertEqual(fixture_budget("qualification").minimum_available_bytes, 800 * 1024**2)
        with self.assertRaises(ValueError):
            fixture_budget("unknown")

    def test_private_acme_budget_covers_client_authority_and_dns_with_the_host_reserve(self):
        budget = fixture_budget('acme')
        self.assertEqual(budget.companions_mib, 128 + 64 + 64 + 96 + 128)
        self.assertEqual(budget.minimum_available_bytes, 800 * 1024**2)
        self.assertEqual(suite_command('acme', None), ('python', 'tools/run_tests.py', 'acme'))

    def test_catalogue_runner_requires_an_existing_in_bundle_evidence_path(self):
        self.assertEqual(suite_command("unit", None), ("python", "tools/run_tests.py", "unit"))
        with self.assertRaises(ValueError):
            suite_command("qualification", None)
        with self.assertRaises(ValueError):
            suite_command("unit", ROOT / "build/evidence.json")
        with patch.object(Path, "is_file", return_value=True):
            with self.assertRaises(ValueError):
                suite_command("qualification", ROOT / "outside-evidence.json")
            self.assertEqual(suite_command("qualification", ROOT / "build/candidate/evidence.json"),
                             ("python", "tools/run_qualification.py",
                              "/app/build/candidate/evidence.json"))

    def run_fixture(self, fixture):
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(fixture)
        return unittest.TextTestRunner(stream=io.StringIO(), resultclass=RecordingResult).run(suite)

    def test_failed_subtest_cannot_be_reported_as_passing(self):
        class FailingCase(unittest.TestCase):
            def test_subtests(self):
                with self.subTest(value=1):
                    self.assertEqual(1, 2)

        result = self.run_fixture(FailingCase)
        self.assertEqual(list(result.records.values()), ["fail"])
        self.assertFalse(result.wasSuccessful())

    def test_skips_and_expected_failures_remain_distinct(self):
        class IncompleteCase(unittest.TestCase):
            @unittest.skip("fixture")
            def test_skip(self):
                self.fail("must not execute")

            @unittest.expectedFailure
            def test_known_failure(self):
                self.fail("fixture")

        result = self.run_fixture(IncompleteCase)
        self.assertEqual(set(result.records.values()), {"skipped", "expected_failure"})

    def test_runtime_reference_is_pinned_until_fixture_completion(self):
        with patch("tools.run_linux.docker") as docker:
            with pinned_runtime_image("sha256:fixture") as reference:
                self.assertTrue(reference.startswith("mail-admin:fixture-"))
                docker.assert_called_once_with(("image", "tag", "sha256:fixture", reference))
            self.assertEqual(docker.call_args.args[0], ("image", "rm", reference))

    def test_runtime_reference_is_released_after_failed_fixture(self):
        with patch("tools.run_linux.docker") as docker:
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                with pinned_runtime_image("sha256:fixture") as reference:
                    raise RuntimeError("fixture failure")
            self.assertEqual(docker.call_args.args[0], ("image", "rm", reference))

    def test_source_identity_includes_launcher_boot_units_and_build_inputs(self):
        baseline, read = source_digest(), Path.read_bytes
        for name in ("host/maddy-launcher", "host/systemd/mail-admin-helper.service", "Makefile"):
            target = ROOT / name

            def changed(path, target=target):
                return b"modified fixture source" if path == target else read(path)

            with patch.object(Path, "read_bytes", changed):
                self.assertNotEqual(source_digest(), baseline, name)
