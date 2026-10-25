import copy
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

from tests.qualification_support import write_bundle
from tests.support import StateFixture
from tools.catalogue_reports import validate_case
from tools.qualification_bundle import QualificationBundle
from tools.qualification_cases import CASE_TESTS, HOST_CASE_TESTS
from tools.qualification_host import (
    HOST_SCOPE,
    host_report,
    merge_host_report,
    source_manifest_digest,
)
from tools.run_host_qualification import host_group, lifecycle_group, qualify


class HostQualificationTests(unittest.TestCase):
    def setUp(self):
        from verification_support import file_digest

        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve()
        self.evidence = write_bundle(self.root, {"host/cli.py": b"synthetic frozen host"})
        self.bundle = QualificationBundle(self.evidence)
        now = datetime.now(UTC).isoformat()
        group = next(group for group in self.bundle.context["catalog"]["groups"]
                     if group["id"] == "T08")
        self.report = {
            "schema_version": 1, "test_id": "T08", **self.bundle.identity,
            "environment": "isolated-linux-amd64", "started_at_utc": now,
            "completed_at_utc": now, "result": "pass", "cases": [],
            "execution": {
                "scope": HOST_SCOPE, "system": "Linux", "machine": "x86_64",
                "runtime_image_id": self.bundle.image.configuration,
                "source_manifest_sha256": source_manifest_digest(self.bundle),
                "installed_manifest_sha256": "a" * 64, "fixture_configuration_sha256": "b" * 64,
                "guest_instance_id": "8a8f1170-b11c-4adf-8626-537e42976ea3",
                "guest_boot_id": "0de6ec2e-9106-4244-9e41-fba30669ec5f",
            },
        }
        for case in group["cases"]:
            tests = HOST_CASE_TESTS.get(case["id"], ())
            self.report["cases"].append({"id": case["id"],
                "result": "pass" if tests else "not_run",
                "assertions": dict.fromkeys(tests, "pass") if tests else []})
        record = next(record for record in self.bundle.context["evidence"]["tests"]
                      if record["id"] == "T08")
        self.path = self.root / "host-report.json"
        self.path.write_text(json.dumps(self.report))
        record.update(report=self.path.name, report_sha256=file_digest(self.path))
        self.record = record

    def publish(self, report):
        from verification_support import file_digest

        self.path.write_text(json.dumps(report))
        self.record["report_sha256"] = file_digest(self.path)
        self.bundle.context["now"] = datetime.now(UTC)

    def test_only_exact_reviewed_host_assertions_can_supply_a_complete_group(self):
        self.publish(self.report)
        local = copy.deepcopy(self.report)
        local["execution"] = {"system": "Linux", "machine": "x86_64"}
        for case in local["cases"]:
            case.update(result="not_run", assertions=[])
        merge_host_report(self.bundle, local)
        self.assertEqual(local["result"], "pass")
        self.assertEqual(sum(case["result"] == "pass" for case in local["cases"]), 4)
        self.assertEqual(local["execution"]["host"], self.report["execution"])

    def test_missing_host_reports_remain_absent_and_tampered_bytes_are_rejected(self):
        self.record.update(report=None, report_sha256=None)
        self.assertIsNone(host_report(self.bundle))
        self.record.update(report=self.path.name, report_sha256="c" * 64)
        with self.assertRaises(ValueError):
            host_report(self.bundle)

    def test_wrong_artifact_image_source_platform_or_disposable_scope_cannot_qualify(self):
        changes = (("artifact_set_sha256", "c" * 64), ("runner_revision", "d" * 64),
                   ("specification_sha256", "e" * 64), ("environment", "target-production"),
                   ("schema_version", True), ("execution.runtime_image_id", "sha256:" + "f" * 64),
                   ("execution.source_manifest_sha256", "f" * 64),
                   ("execution.scope", "production"), ("execution.machine", "aarch64"),
                   ("execution.guest_instance_id", "not-an-instance"),
                   ("execution.installed_manifest_sha256", None))
        for path, value in changes:
            changed = copy.deepcopy(self.report)
            parent, _, key = path.rpartition(".")
            (changed[parent] if parent else changed)[key] = value
            self.publish(changed)
            with self.subTest(path=path), self.assertRaises((ValueError, KeyError)):
                host_report(self.bundle)

    def test_skipped_missing_duplicate_or_invented_assertions_cannot_become_passes(self):
        variants = []
        skipped = copy.deepcopy(self.report)
        case = next(case for case in skipped["cases"] if case["id"] == "T08.02")
        case["assertions"] = dict.fromkeys(case["assertions"], "skip")
        variants.append(skipped)
        invented = copy.deepcopy(self.report)
        invented["cases"][0].update(result="pass", assertions={"manual-prototype": "pass"})
        variants.append(invented)
        missing = copy.deepcopy(self.report)
        missing["cases"].pop()
        variants.append(missing)
        duplicate = copy.deepcopy(self.report)
        duplicate["cases"].append(duplicate["cases"][0])
        variants.append(duplicate)
        for changed in variants:
            self.publish(changed)
            with self.subTest(cases=changed["cases"]), self.assertRaises(ValueError):
                host_report(self.bundle)

    def test_stale_future_or_reversed_host_observations_are_rejected(self):
        now = datetime.now(UTC)
        for started, completed in ((now - timedelta(days=2), now),
                                   (now, now + timedelta(minutes=1)),
                                   (now, now - timedelta(seconds=1))):
            changed = {**self.report, "started_at_utc": started.isoformat(),
                       "completed_at_utc": completed.isoformat()}
            self.publish(changed)
            with self.subTest(started=started, completed=completed), self.assertRaises(ValueError):
                host_report(self.bundle)

    def test_reboot_is_observed_before_other_checks_can_restart_consumers(self):
        original = next(group for group in self.bundle.context['catalog']['groups']
                        if group['id'] == 'T08')
        before = copy.deepcopy(original)
        observed = lifecycle_group(self.bundle)
        self.assertEqual(observed['cases'][0]['id'], 'T08.04')
        self.assertEqual({case['id'] for case in observed['cases']},
                         {case['id'] for case in original['cases']})
        self.assertEqual(original, before)

    def retention_report(self):
        group = host_group(self.bundle, 'T22')
        cases = [{'id': case['id'], 'result': 'not_run', 'assertions': []}
                 for case in group['cases']]
        selected = next(case for case in cases if case['id'] == 'T22.02')
        selected.update(result='pass', assertions=dict.fromkeys(HOST_CASE_TESTS['T22.02'], 'pass'))
        return {**copy.deepcopy(self.report), 'test_id': 'T22', 'cases': cases, 'result': 'blocked'}

    def test_host_retention_report_cannot_fill_unrelated_local_assertions(self):
        observed = self.retention_report()
        self.record = next(record for record in self.bundle.context['evidence']['tests']
                           if record['id'] == 'T22')
        self.record['report'] = self.path.name
        self.publish(observed)
        self.assertEqual(host_report(self.bundle, 'T22'), observed)
        changed = copy.deepcopy(observed)
        case = next(case for case in changed['cases'] if case['id'] == 'T22.02')
        case['assertions'].update(dict.fromkeys(CASE_TESTS['T22.02'], 'pass'))
        self.publish(changed)
        with self.assertRaisesRegex(ValueError, 'assertions differ'):
            host_report(self.bundle, 'T22')

    def test_a_composite_case_requires_both_exact_local_and_host_assertions(self):
        local = self.retention_report()
        case = next(case for case in local['cases'] if case['id'] == 'T22.02')
        case['assertions'] = dict.fromkeys(CASE_TESTS['T22.02'], 'pass')
        with patch('tools.qualification_host.host_report', return_value=self.retention_report()):
            merge_host_report(self.bundle, local)
        combined = next(case for case in local['cases'] if case['id'] == 'T22.02')
        validate_case(combined, local['execution'])
        self.assertEqual(set(combined['assertions']),
                         set(CASE_TESTS['T22.02'] + HOST_CASE_TESTS['T22.02']))
        for missing in CASE_TESTS['T22.02'] + HOST_CASE_TESTS['T22.02']:
            changed = copy.deepcopy(combined)
            del changed['assertions'][missing]
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                validate_case(changed, local['execution'])

    def test_missing_host_retention_stays_not_run_even_after_local_checks_pass(self):
        local = self.retention_report()
        local['execution'] = {'system': 'Linux', 'machine': 'x86_64'}
        case = next(case for case in local['cases'] if case['id'] == 'T22.02')
        case['assertions'] = dict.fromkeys(CASE_TESTS['T22.02'], 'pass')
        with self.assertRaisesRegex(ValueError, 'Missing host'):
            validate_case(case, local['execution'])
        with patch('tools.qualification_host.host_report', return_value=None):
            merge_host_report(self.bundle, local)
        self.assertEqual(case, {'id': 'T22.02', 'result': 'not_run', 'assertions': []})
        validate_case(case, local['execution'])
        self.assertEqual(local['result'], 'blocked')

    def test_host_runner_accepts_only_groups_with_registered_host_assertions(self):
        self.assertEqual(host_group(self.bundle, 'T22')['id'], 'T22')
        for identifier in ('T17', '../outside', 'P01'):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                host_group(self.bundle, identifier)

    def test_missing_host_evidence_cannot_hide_a_failed_local_retention_assertion(self):
        local = self.retention_report()
        local['execution'] = {'system': 'Linux', 'machine': 'x86_64'}
        case = next(case for case in local['cases'] if case['id'] == 'T22.02')
        case.update(result='fail', assertions=dict.fromkeys(CASE_TESTS['T22.02'], 'pass'))
        case['assertions'][CASE_TESTS['T22.02'][0]] = 'error'
        before = copy.deepcopy(case)
        with patch('tools.qualification_host.host_report', return_value=None):
            merge_host_report(self.bundle, local)
        self.assertEqual(case, before)
        validate_case(case, local['execution'])
        case['assertions'][CASE_TESTS['T22.02'][0]] = 'pass'
        with self.assertRaisesRegex(ValueError, 'Missing host'):
            validate_case(case, local['execution'])

    def test_post_run_binding_failure_keeps_raw_assertions_without_qualifying_references(self):
        with (patch('tools.run_host_qualification.GuestHelper.load', return_value=Mock()),
              patch('tools.run_host_qualification.QualificationBundle', return_value=self.bundle),
              patch.object(self.bundle, 'verify_source'),
              patch('tools.run_host_qualification.group_report', return_value=self.report),
              patch('tools.run_host_qualification.verify_host_installation',
                    side_effect=[{}, RuntimeError('Changed installed fixture')]),
              patch.dict('os.environ', {})):
            with self.assertRaisesRegex(RuntimeError, 'Changed installed fixture'):
                qualify(self.evidence)
        reports = list(self.root.glob('host-qualification-*/assertion-results.json'))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text()), self.report)
        self.assertFalse(list(self.root.glob('host-qualification-*/T08.json')))
        self.assertFalse(list(self.root.glob('host-qualification-*-evidence.json')))
