import copy
import json
import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import StateFixture
from tools.catalogue_assertions import (
    assertion_modules,
    group_from_modules,
    read_modules,
    validate_controller,
    validate_module_inventory,
    validate_module_report,
)


class CatalogueAssertionFixture(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root
        self.module = 'tests.maddy.test_restic_restore'
        self.test = self.module + '.NativeResticRestoreTests.test_fixture'
        self.modules = {self.module: [self.test]}
        self.bundle = SimpleNamespace(identity={'artifact_set_sha256': 'a' * 64,
                                                'specification_sha256': 'b' * 64,
                                                'runner_revision': 'c' * 64}, context={
            'now': datetime(2026, 10, 24, 0, 1, tzinfo=UTC),
            'contract': {'evidence_max_age_seconds': 86400},
        })
        self.report = {'schema_version': 1, 'test_id': self.module, **self.bundle.identity,
                       'environment': 'isolated-linux-amd64', 'result': 'pass',
                       'started_at_utc': '2026-10-24T00:00:00+00:00',
                       'completed_at_utc': '2026-10-24T00:00:30+00:00',
                       'cases': [{'id': self.test, 'result': 'pass',
                                  'assertions': {self.test: 'pass'}}],
                       'execution': {'system': 'Linux', 'machine': 'x86_64',
                                     'runtime_image': 'runtime', 'qualification_image': 'suite',
                                     'controller_memory': {'state': 'observed',
                                                          'limit_bytes': 192 * 1024**2,
                                                          'peak_bytes': 150 * 1024**2}}}
        self.records = [{'group': self.module, 'container_id': 'd' * 64,
                         'container_name': 'mail-admin-catalogue-controller-'
                                           '00000000-0000-0000-0000-000000000001'},
                        {'state': {'Running': False, 'OOMKilled': False, 'ExitCode': 0},
                         'attached_exit_code': 0}]
        self.boundary = patch.dict(os.environ, {'MAIL_ADMIN_RUNTIME_IMAGE': 'runtime',
                                              'MAIL_ADMIN_QUALIFICATION_IMAGE': 'suite'})
        self.boundary.start()
        self.addCleanup(self.boundary.stop)

class CatalogueAssertionTests(CatalogueAssertionFixture):
    def validate(self, report):
        validate_module_report(self.bundle, (self.module, [self.test]), report)

    def test_inventory_deduplicates_shared_assertions_without_omitting_any(self):
        mapping = {'T01.01': (self.test,), 'T02.01': (self.test,)}
        self.assertEqual(assertion_modules(mapping), self.modules)
        self.assertEqual(validate_module_inventory({'assertion_modules': self.modules}),
                         self.modules)
        for modules in ({}, {'../escape': [self.test]}, {self.module: [self.test, self.test]},
                        {self.module: ['tests.unit.test_foreign.Case.test_other']}):
            with self.subTest(modules=modules), self.assertRaises(ValueError):
                validate_module_inventory({'assertion_modules': modules})

    def test_exact_module_outcomes_require_matching_source_image_and_original_limit(self):
        self.validate(self.report)
        for change in ({'runner_revision': 'f' * 64}, {'cases': []},
                       {'cases': self.report['cases'] * 2}, {'schema_version': True},
                       {'execution': {**self.report['execution'], 'runtime_image': 'other'}},
                       {'execution': {**self.report['execution'], 'controller_memory': {
                           'state': 'observed', 'limit_bytes': 256 * 1024**2}}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.validate({**self.report, **change})

    def test_failed_skipped_or_wrong_assertions_cannot_be_promoted_by_assembly(self):
        for outcome in ('fail', 'error', 'skipped', 'expected_failure', 'unexpected_success'):
            report = copy.deepcopy(self.report)
            report['cases'][0].update(result='fail', assertions={self.test: outcome})
            report['result'] = 'blocked'
            self.validate(report)
            with self.assertRaises(ValueError):
                self.validate({**report, 'result': 'pass'})
        wrong = {**self.report, 'cases': [{'id': self.test, 'result': 'pass',
                                          'assertions': {'foreign': 'pass'}}]}
        with self.assertRaises(ValueError):
            self.validate(wrong)

    def test_observed_oom_or_unknown_controller_blocks_even_a_passing_report(self):
        validate_controller(self.module, self.records, self.report)
        for state in ({'OOMKilled': True}, {'Running': True}, {'ExitCode': 137},
                      {'ExitCode': False}):
            records = copy.deepcopy(self.records)
            records[-1]['state'].update(state)
            with self.subTest(state=state), self.assertRaises(ValueError):
                validate_controller(self.module, records, self.report)
        with self.assertRaises(ValueError):
            validate_controller(self.module, self.records[:1], self.report)

    def test_missing_raw_module_or_controller_record_cannot_complete_assembly(self):
        with patch('tools.catalogue_assertions.assertion_modules', return_value=self.modules):
            with self.assertRaises(FileNotFoundError):
                read_modules(self.bundle, self.root)
            path = self.root / (self.module + '.json')
            path.write_text(json.dumps(self.report))
            with self.assertRaises(FileNotFoundError):
                read_modules(self.bundle, self.root)
            progress = self.root / (self.module + '-controller.jsonl')
            progress.write_text('\n'.join(json.dumps(row) for row in self.records) + '\n')
            self.assertEqual(read_modules(self.bundle, self.root), {self.module: self.report})

    def test_only_recorded_passes_fill_cases_and_original_reports_are_referenced(self):
        path = self.root / (self.module + '.json')
        path.write_text(json.dumps(self.report))
        group = {'id': 'T01', 'environment': 'isolated-linux-amd64',
                 'cases': [{'id': 'T01.01'}, {'id': 'T01.02'}]}
        mapping = {'T01.01': (self.test,)}
        with patch.dict('tools.catalogue_assertions.CASE_TESTS', mapping, clear=True):
            report = group_from_modules(self.bundle, group,
                                        (self.root, {self.module: self.report}))
        self.assertEqual([case['result'] for case in report['cases']], ['pass', 'not_run'])
        self.assertEqual(report['result'], 'blocked')
        self.assertEqual(report['cases'][0]['assertions'], {self.test: 'pass'})
        self.assertEqual(report['started_at_utc'], self.report['started_at_utc'])
        self.assertEqual(report['execution']['assertion_modules'][self.module]['report'],
                         self.module + '.json')
        self.assertEqual(json.loads(path.read_bytes()), self.report)
