"""Controller death and lost reports must not turn into catalogue passes."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.catalogue_modules import GroupController, catalogue_inventory


class ControllerDocker:
    def __init__(self):
        self.calls, self.names = [], {}
        self.state = {'Running': False, 'ExitCode': 0, 'OOMKilled': False}
        self.foreign = False

    def __call__(self, arguments):
        self.calls.append(arguments)
        if arguments[0] == 'create':
            name = arguments[arguments.index('--name') + 1]
            identifier = f'{len(self.names) + 1:064x}'
            self.names[identifier] = name
            return identifier
        if arguments[0] == 'inspect':
            identifier = arguments[1]
            name = self.names[identifier]
            return json.dumps([{'Id': identifier, 'Name': '/foreign' if self.foreign else '/'+name,
                                'Config': {'Image': 'qualification-image', 'Labels': {
                                    'mail-admin.catalogue-controller': name}},
                                'State': self.state}])
        if arguments[0] == 'rm':
            return arguments[1]
        raise AssertionError('Unexpected controller command')


class CatalogueControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.docker = ControllerDocker()
        self.command = ('run', '--rm', '--memory', '192m', 'qualification-image')

    def job(self, group):
        return GroupController((group, self.root),
                               (self.command, ('python', 'runner'), self.docker))

    def run_job(self, job, code):
        with patch('tools.catalogue_modules.subprocess.run',
                   return_value=SimpleNamespace(returncode=code)):
            job.run('/app/build/private-group-output')

    def test_each_group_has_a_distinct_controller_and_preserves_the_memory_limit(self):
        jobs = [self.job(group) for group in ('T01', 'T09')]
        for job in jobs:
            (self.root / (job.group + '.json')).write_text(json.dumps({'test_id': job.group}))
            self.run_job(job, 0)
        self.assertNotEqual(jobs[0].identifier, jobs[1].identifier)
        self.assertNotEqual(jobs[0].name, jobs[1].name)
        creates = [call for call in self.docker.calls if call[0] == 'create']
        self.assertTrue(all(call[call.index('--memory') + 1] == '192m' for call in creates))

    def test_oom_is_recorded_as_failure_even_when_a_report_already_exists(self):
        self.docker.state.update({'ExitCode': 137, 'OOMKilled': True})
        job = self.job('T09')
        (self.root / 'T09.json').write_text(json.dumps({'test_id': 'T09', 'result': 'pass'}))
        with self.assertRaisesRegex(RuntimeError, 'killed or failed controller'):
            self.run_job(job, 137)
        outcome = json.loads(job.progress.read_text().splitlines()[-1])
        self.assertTrue(outcome['state']['OOMKilled'])
        self.assertEqual(outcome['attached_exit_code'], 137)

    def test_zero_exit_without_the_group_report_cannot_complete(self):
        with self.assertRaisesRegex(RuntimeError, 'missing group report'):
            self.run_job(self.job('T09'), 0)

    def test_foreign_identity_cannot_be_observed_as_success_or_cleaned_up(self):
        self.docker.foreign = True
        with self.assertRaisesRegex(RuntimeError, 'untrusted catalogue controller'):
            self.run_job(self.job('T09'), 0)
        self.assertFalse(any(call[0] == 'rm' for call in self.docker.calls))

    def test_surviving_or_timed_out_controllers_remain_unknown_and_are_not_removed(self):
        job = self.job('T09')
        self.docker.state['Running'] = True
        with self.assertRaisesRegex(RuntimeError, 'outcome remains unknown'):
            self.run_job(job, 0)
        self.assertFalse(any(call[0] == 'rm' for call in self.docker.calls))
        other = self.job('T10')
        with patch('tools.catalogue_modules.subprocess.run',
                   side_effect=subprocess.TimeoutExpired('private controller', 900)):
            with self.assertRaises(subprocess.TimeoutExpired):
                other.run('/app/build/private-group-output')
        self.assertEqual(json.loads(other.progress.read_text().splitlines()[-1])['outcome'],
                         'completion_unknown')

    def test_missing_groups_or_malformed_case_identifiers_cannot_select_partial_qualification(self):
        inventory = {'groups': {f'T{number:02d}': [f'T{number:02d}.01']
                                for number in range(1, 23)}}
        with patch.dict(inventory, {'groups': {}}):
            with self.assertRaises(ValueError):
                catalogue_inventory((), (), lambda _: json.dumps(inventory))
        for values in ([], [['invalid']], ['T01.01', 'T01.01'], ['../outside']):
            with self.subTest(values=values):
                malformed = {'groups': {**inventory['groups'], 'T01': values}}
                with self.assertRaises(ValueError):
                    catalogue_inventory((), (), lambda _, value=malformed: json.dumps(value))
