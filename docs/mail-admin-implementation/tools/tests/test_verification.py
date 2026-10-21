"""Check verifier failure behavior and expose counterexamples to unsafe model variants."""

import copy
import contextlib
import io
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from dataclasses import replace
from datetime import datetime, timedelta, timezone

TOOLS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from check_models import (
    Authentication, Coordination, Creation, authentication_invariant,
    authentication_steps, coordination_invariant, coordination_steps,
    creation_invariant, creation_steps, explore, run_models,
)
from check_release import verify_evidence
from evidence_fixtures import EvidenceFixture
from verification_support import PACKAGE_ROOT, ValidationError, inside_file, read_json
from verify_plan import check_dag, validate_catalogue, validate_markdown, validate_work_package_table
from verify_plan import main as verify_plan_main


def unsafe_authentication(state):
    result = authentication_steps(state)
    if state.verified:
        result.append(('unchecked_completion', replace(state, session_revision=state.worker_revision,
                                                        session_valid=True, worker_revision=-1, verified=False)))
    return result


def unsafe_creation(state):
    result = creation_steps(state)
    if state.phase == 'failed' and state.storage:
        result.append(('remove_credentials_despite_storage', replace(state, credentials=False)))
    return result


def unsafe_coordination(state):
    result = coordination_steps(state)
    if not state.renewal_owner and not state.maddy_owner:
        result.append(('backup_ignores_unknown_child', replace(state, renewal_owner='backup',
                       maddy_owner='backup', backup_capturing=True)))
    return result


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='mail-admin-unit-fixture-')
        self.addCleanup(self.temporary.cleanup)
        self.now = datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc)
        self.bundle = EvidenceFixture(pathlib.Path(self.temporary.name), self.now)

    def verify(self, phase='release'):
        return verify_evidence(self.bundle.save(), phase, self.now)

    def test_synthetic_metadata_qualifies_only_the_evidence_format(self):
        self.assertEqual(self.verify()['result'], 'pass')
        self.assertEqual(self.verify('preflight')['result'], 'pass')
        self.assertEqual(self.verify('candidate')['result'], 'pass')

    def test_real_unexecuted_template_is_blocked(self):
        template = PACKAGE_ROOT / 'verification/runtime-evidence.template.json'
        with self.assertRaises(ValidationError):
            verify_evidence(template, 'release', self.now)

    def test_missing_report_blocks(self):
        self.bundle.record('T01')['report'] = None
        with self.assertRaises(ValidationError):
            self.verify()

    def test_missing_case_blocks_even_when_group_says_pass(self):
        report = self.bundle.report('T01')
        report['cases'].pop()
        self.bundle.save_report('T01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_skipped_case_blocks_even_when_group_says_pass(self):
        report = self.bundle.report('T03')
        report['cases'][0]['result'] = 'skipped'
        self.bundle.save_report('T03', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_duplicate_case_blocks(self):
        report = self.bundle.report('T03')
        report['cases'].append(copy.deepcopy(report['cases'][0]))
        self.bundle.save_report('T03', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_wrong_artifact_binding_blocks(self):
        report = self.bundle.report('T01')
        report['artifact_set_sha256'] = 'f' * 64
        self.bundle.save_report('T01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_wrong_specification_binding_blocks(self):
        self.bundle.document['specification_sha256'] = 'f' * 64
        with self.assertRaises(ValidationError):
            self.verify()

    def test_wrong_environment_blocks(self):
        report = self.bundle.report('P01')
        report['environment'] = 'isolated-linux-amd64'
        self.bundle.save_report('P01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_stale_evidence_blocks(self):
        report = self.bundle.report('T01')
        report['started_at_utc'] = (self.now - timedelta(days=2)).isoformat()
        self.bundle.save_report('T01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_future_evidence_blocks(self):
        report = self.bundle.report('T01')
        report['completed_at_utc'] = (self.now + timedelta(seconds=1)).isoformat()
        self.bundle.save_report('T01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_modified_report_digest_blocks(self):
        record = self.bundle.record('T01')
        (self.bundle.root / record['report']).write_text('{}')
        with self.assertRaises(ValidationError):
            self.verify()

    def test_modified_artifact_digest_blocks(self):
        record = self.bundle.document['artifacts'][0]
        (self.bundle.root / record['path']).write_text('changed')
        with self.assertRaises(ValidationError):
            self.verify()

    def test_enabled_email_otp_requires_delivery_evidence(self):
        self.bundle.inputs['email_otp_enabled'] = True
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_enabled_delete_requires_qualifying_test_evidence(self):
        self.bundle.inputs['permanent_deletion_enabled'] = True
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_direct_mx_requires_approval(self):
        self.bundle.inputs['mx_mode'] = 'direct'
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_unsafe_memory_budget_blocks(self):
        self.bundle.inputs['memory_limit_bytes'] = self.bundle.inputs['host_memory_bytes']
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_boolean_cannot_substitute_for_numeric_uid(self):
        self.bundle.inputs['service_uid'] = True
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_gate_chronology_blocks_hardening_before_backup(self):
        report = self.bundle.report('G05')
        report['started_at_utc'] = (self.now - timedelta(hours=4)).isoformat()
        self.bundle.save_report('G05', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_approval_before_evidence_blocks(self):
        self.bundle.document['approval']['approved_at_utc'] = (self.now - timedelta(hours=4)).isoformat()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_missing_approval_blocks(self):
        self.bundle.document['approval'] = None
        with self.assertRaises(ValidationError):
            self.verify()

    def test_path_escape_blocks(self):
        with self.assertRaises(ValidationError):
            inside_file(self.bundle.root, '../outside-file')

    def test_duplicate_json_keys_block(self):
        file = self.bundle.root / 'duplicate.json'
        file.write_text('{"result": "fail", "result": "pass"}')
        with self.assertRaises(ValidationError):
            read_json(file)

    def test_symlink_escape_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = pathlib.Path(directory) / 'outside.json'
            outside.write_text('{}')
            (self.bundle.root / 'escape').symlink_to(outside)
            with self.assertRaises(ValidationError):
                inside_file(self.bundle.root, 'escape')

    def test_wrong_runner_bundle_binding_blocks(self):
        self.bundle.document['runner_revision'] = 'f' * 64
        with self.assertRaises(ValidationError):
            self.verify()

    def test_configuration_input_mismatch_blocks(self):
        self.bundle.document['inputs']['service_uid'] += 1
        with self.assertRaises(ValidationError):
            self.verify()

    def test_missing_baseline_gate_blocks(self):
        self.bundle.document['gates'] = [record for record in self.bundle.document['gates']
                                         if record['id'] != 'G04']
        with self.assertRaises(ValidationError):
            self.verify()

    def test_approved_direct_mx_still_requires_cutover_evidence(self):
        self.bundle.inputs.update({'mx_mode': 'direct', 'mx_cutover_approved': True})
        self.bundle.refresh()
        with self.assertRaises(ValidationError):
            self.verify()

    def test_target_evidence_cannot_predate_startup(self):
        report = self.bundle.report('P01')
        report['started_at_utc'] = (self.now - timedelta(hours=3)).isoformat()
        self.bundle.save_report('P01', report)
        with self.assertRaises(ValidationError):
            self.verify()

    def test_report_cannot_override_its_declared_reference(self):
        record = self.bundle.record('T01')
        record.update({'path': 'unexpected.json', 'sha256': 'f' * 64})
        with self.assertRaises(ValidationError):
            self.verify()


class SpecificationTests(unittest.TestCase):
    def test_all_safe_models_pass(self):
        self.assertEqual(len(run_models()), 3)

    def test_old_revision_counterexample_is_found(self):
        with self.assertRaisesRegex(ValidationError, 'change_password'):
            explore(Authentication(), unsafe_authentication, authentication_invariant)

    def test_storage_preservation_counterexample_is_found(self):
        with self.assertRaisesRegex(ValidationError, 'remove_credentials_despite_storage'):
            explore(Creation(), unsafe_creation, creation_invariant)

    def test_orphan_child_counterexample_is_found(self):
        with self.assertRaisesRegex(ValidationError, 'backup_ignores_unknown_child'):
            explore(Coordination(), unsafe_coordination, coordination_invariant)

    def test_dependency_cycle_is_rejected(self):
        with self.assertRaises(ValidationError):
            check_dag({'first': ['second'], 'second': ['first']})

    def test_unknown_dependency_is_rejected(self):
        with self.assertRaises(ValidationError):
            check_dag({'first': ['missing']})

    def test_missing_catalogue_group_is_rejected(self):
        catalogue = read_json(PACKAGE_ROOT / 'verification/test-catalog.json')
        catalogue['groups'].pop()
        with self.assertRaises(ValidationError):
            validate_catalogue(PACKAGE_ROOT, catalogue)

    def test_broken_document_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            file = root / 'bad.md'
            file.write_text('# Broken\n\n[missing](missing.md)\n')
            with self.assertRaises(ValidationError):
                validate_markdown(file, root)

    def test_work_package_dependency_drift_is_rejected(self):
        contract = read_json(PACKAGE_ROOT / 'requirements/contracts.json')
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / 'implementation').mkdir()
            source = (PACKAGE_ROOT / 'implementation/work-packages.md').read_text()
            source = source.replace('W02, W03, W04', 'W01')
            (root / 'implementation/work-packages.md').write_text(source)
            with self.assertRaises(ValidationError):
                validate_work_package_table(root, contract)

    def test_tool_timeout_returns_a_failed_check(self):
        timeout = subprocess.TimeoutExpired(['synthetic-unit-command'], 1)
        arguments = ['verify_plan.py', '--run-tests']
        with mock.patch.object(sys, 'argv', arguments), mock.patch('verify_plan.subprocess.run', side_effect=timeout):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(verify_plan_main(), 1)
        self.assertIn('"result": "failed"', output.getvalue())


if __name__ == '__main__':
    unittest.main()
