"""Assembly rejects omitted groups, foreign artifacts and fabricated passing assertions."""

import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.catalogue_reports import report_directory, validate_group
from tools.qualification_cases import CASE_TESTS
from tools.run_qualification import assemble_groups


class CatalogueReportTests(unittest.TestCase):
    def setUp(self):
        self.bundle = SimpleNamespace(identity={'artifact_set_sha256': 'a' * 64,
                                                'specification_sha256': 'b' * 64,
                                                'runner_revision': 'c' * 64})
        self.group = {'id': 'T02', 'environment': 'isolated-linux-amd64',
                      'cases': [{'id': 'T02.01'}]}
        self.report = {'schema_version': 1, 'test_id': 'T02', **self.bundle.identity,
                       'environment': self.group['environment'], 'result': 'pass',
                       'cases': [{'id': 'T02.01', 'result': 'pass',
                                  'assertions': dict.fromkeys(CASE_TESTS['T02.01'], 'pass')}],
                       'execution': {'system': 'Linux', 'machine': 'x86_64',
                                     'runtime_image': 'runtime', 'qualification_image': 'suite'}}
        self.environment = {'MAIL_ADMIN_RUNTIME_IMAGE': 'runtime',
                            'MAIL_ADMIN_QUALIFICATION_IMAGE': 'suite'}

    def validate(self, report):
        with patch.dict(os.environ, self.environment):
            validate_group(self.bundle, self.group, report)

    def test_complete_matching_assertions_are_accepted(self):
        self.validate(self.report)

    def test_wrong_artifacts_schema_or_image_cannot_reuse_another_groups_pass(self):
        records = [{**self.report, 'runner_revision': 'd' * 64},
                   {**self.report, 'schema_version': True},
                   {**self.report, 'execution': {**self.report['execution'],
                                                 'runtime_image': 'replacement'}}]
        for record in records:
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.validate(record)

    def test_missing_repeated_failed_or_skipped_assertions_cannot_be_passing(self):
        for outcome in ({}, dict.fromkeys(CASE_TESTS['T02.01'], 'fail'),
                        dict.fromkeys(CASE_TESTS['T02.01'], 'skipped')):
            record = copy.deepcopy(self.report)
            record['cases'][0]['assertions'] = outcome
            with self.subTest(assertions=outcome), self.assertRaises(ValueError):
                self.validate(record)
        for cases in ([], self.report['cases'] * 2):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                self.validate({**self.report, 'cases': cases})

    def test_registered_cases_cannot_be_hidden_as_not_run(self):
        record = {**self.report, 'result': 'blocked',
                  'cases': [{'id': 'T02.01', 'result': 'not_run', 'assertions': []}]}
        with self.assertRaisesRegex(ValueError, 'silently become not_run'):
            self.validate(record)

    def test_real_failed_assertions_remain_blocked(self):
        record = copy.deepcopy(self.report)
        record['cases'][0].update({'result': 'fail', 'assertions':
                                  dict.fromkeys(CASE_TESTS['T02.01'], 'fail')})
        record['result'] = 'blocked'
        self.validate(record)
        with self.assertRaises(ValueError):
            self.validate({**record, 'result': 'pass'})

    def test_absent_group_stops_assembly_before_final_evidence_is_written(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / ('qualification-' + str(uuid.uuid4()))
            directory.mkdir()
            (directory / 'T02.json').write_text(json.dumps(self.report))
            self.bundle.context = {'catalog': {'groups': [self.group, {
                'id': 'T03', 'environment': self.group['environment'], 'cases': [],
            }]}}
            with (patch('tools.run_qualification.load_bundle', return_value=self.bundle),
                  patch('tools.run_qualification.finish_catalogue') as finish,
                  patch.dict(os.environ, self.environment)):
                with self.assertRaises(FileNotFoundError):
                    assemble_groups(root / 'evidence.json', directory)
            finish.assert_not_called()

    def test_output_cannot_escape_the_frozen_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / ('qualification-' + str(uuid.uuid4()))
            directory.mkdir()
            self.assertEqual(report_directory(root / 'evidence.json', directory),
                             directory.resolve())
            with self.assertRaises(ValueError):
                report_directory(root / 'other/evidence.json', directory)
