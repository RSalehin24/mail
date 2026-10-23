import hashlib
import io
import unittest
from unittest.mock import patch

from tests.qualification_support import write_bundle
from tests.support import StateFixture
from tools.qualification_bundle import QualificationBundle
from tools.run_qualification import CaseAssertions, _update_references, group_report, run_case


class QualificationRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve()

    def source_bundle(self):
        source = {"host/cli.py": b"frozen fixture code"}
        evidence = write_bundle(self.root, source)
        identities = {name: hashlib.sha256(value).hexdigest() for name, value in source.items()}
        return QualificationBundle(evidence), identities

    def test_frozen_suite_binds_executing_files_and_the_exact_source_set(self):
        bundle, identities = self.source_bundle()
        with patch("tools.qualification_bundle.source_identities", return_value=identities):
            bundle.verify_source()
        for changed in ({}, {**identities, "host/additional.py": "a" * 64},
                        {"host/cli.py": "b" * 64}):
            with patch("tools.qualification_bundle.source_identities", return_value=changed):
                with self.assertRaises(ValueError):
                    bundle.verify_source()

    def test_changed_artifacts_cannot_be_reused_with_an_old_identity(self):
        bundle, _ = self.source_bundle()
        (self.root / "deployment.json").write_text('{"changed":true}')
        with self.assertRaises(ValueError):
            bundle.verify_artifacts()

    def test_unimplemented_cases_stay_not_run_even_when_another_assertion_passes(self):
        bundle, _ = self.source_bundle()
        group = {"id": "T03", "environment": "isolated-linux-amd64",
                 "cases": [{"id": "T03.01"}, {"id": "T03.06"}]}
        passing = {"id": "T03.06", "result": "pass", "assertions": {"real_test": "pass"}}
        with (patch.object(bundle, "verify_source"), patch.object(bundle, "verify_artifacts"),
              patch("tools.run_qualification.run_case", side_effect=[
                  {"id": "T03.01", "result": "not_run", "assertions": []}, passing
              ])):
            report = group_report(bundle, group, {'T03.06': ('real_test',)})
        self.assertEqual(report["result"], "blocked")
        self.assertEqual(report["cases"][0]["result"], "not_run")
        self.assertEqual(report["cases"][1]["assertions"], {'real_test': 'pass'})
        self.assertEqual(report["artifact_set_sha256"], bundle.identity["artifact_set_sha256"])
        self.assertIn("docker_engine_architecture", report["execution"])
        self.assertIn("qualification_image", report["execution"])

    def test_case_executor_records_real_pass_failure_skip_and_subtest_failure(self):
        class Passing(unittest.TestCase):
            def test_assertion(self):
                self.assertEqual(1 + 1, 2)

        class Incomplete(unittest.TestCase):
            @unittest.skip("synthetic omitted assertion")
            def test_assertion(self):
                self.fail("must not run")

        class Failing(unittest.TestCase):
            def test_assertion(self):
                with self.subTest(boundary="synthetic"):
                    self.assertEqual(1, 2)

        for case, expected in ((Passing, "pass"), (Incomplete, "fail"), (Failing, "fail")):
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
            identifier = next(iter(suite)).id()
            with (patch("tools.run_qualification.unittest.defaultTestLoader.loadTestsFromNames",
                        return_value=suite), patch("sys.stderr", new_callable=io.StringIO)):
                report = run_case("T03.06", (identifier,))
            self.assertEqual(report["result"], expected)
        self.assertEqual(run_case("T03.01", ())["result"], "not_run")

    def test_new_evidence_references_stay_inside_the_original_artifact_bundle(self):
        from verification_support import inside_file

        directory = self.root / "qualification-unique"
        directory.mkdir()
        (directory / "T03.json").write_text('{"result":"blocked"}')
        evidence = {"artifacts": [{"path": "dashboard.tar"}],
                    "tests": [{"id": "T03", "report": None, "report_sha256": None}]}
        _update_references(evidence, {"T03": {}}, directory)
        self.assertEqual(evidence["artifacts"], [{"path": "dashboard.tar"}])
        self.assertEqual(inside_file(self.root, evidence["tests"][0]["report"]),
                         directory / "T03.json")

    def test_shared_assertions_execute_once_per_run_and_each_case_retains_exact_assertions(self):
        mapping = {'T07.01': ('shared',), 'T07.02': ('shared', 'additional')}
        runner = CaseAssertions(mapping)
        with patch('tools.run_qualification.run_case', side_effect=lambda identifier, tests: {
            'id': identifier, 'result': 'pass', 'assertions': dict.fromkeys(tests, 'pass'),
        }) as execute:
            self.assertEqual(runner.run('T07.01')['assertions'], {'shared': 'pass'})
            self.assertEqual(runner.run('T07.02')['assertions'],
                             {'shared': 'pass', 'additional': 'pass'})
            self.assertEqual(runner.run('T07.01')['result'], 'pass')
            self.assertEqual(execute.call_args_list, [
                unittest.mock.call('T07.01', ('shared',)),
                unittest.mock.call('T07.02', ('additional',)),
            ])
            CaseAssertions(mapping).run('T07.01')
            self.assertEqual(execute.call_count, 3)

    def test_failed_skipped_or_mismatched_assertions_cannot_enter_the_run_cache(self):
        mapping = {'T07.01': ('shared',), 'T07.02': ('shared',)}
        for outcome in ('fail', 'skipped'):
            runner = CaseAssertions(mapping)
            with patch('tools.run_qualification.run_case', return_value={
                'id': 'T07.01', 'result': 'fail', 'assertions': {'shared': outcome},
            }) as execute:
                self.assertEqual(runner.run('T07.01')['result'], 'fail')
                self.assertEqual(runner.run('T07.02')['result'], 'fail')
                self.assertEqual(execute.call_count, 2)
            self.assertEqual(runner.passed, set())
        with patch('tools.run_qualification.run_case', return_value={
            'id': 'T07.01', 'result': 'pass', 'assertions': {'another': 'pass'},
        }):
            with self.assertRaisesRegex(ValueError, 'Incomplete assertion execution'):
                CaseAssertions(mapping).run('T07.01')

    def test_reused_assertions_keep_the_actual_execution_start_in_later_group_reports(self):
        bundle, _ = self.source_bundle()
        runner = CaseAssertions({'T07.01': ('shared',), 'T07.02': ('shared',)})
        runner.started_at = '2026-10-23T00:00:00+00:00'
        group = {'id': 'T07', 'environment': 'isolated-linux-amd64',
                 'cases': [{'id': 'T07.01'}, {'id': 'T07.02'}]}
        with (patch.object(bundle, 'verify_source'), patch.object(bundle, 'verify_artifacts'),
              patch('tools.run_qualification.run_case', return_value={
                  'id': 'T07.01', 'result': 'pass', 'assertions': {'shared': 'pass'},
              })):
            report = group_report(bundle, group, runner)
        self.assertEqual(report['started_at_utc'], runner.started_at)
        self.assertEqual(report['result'], 'pass')
