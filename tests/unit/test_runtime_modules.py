"""A complete runtime result requires every fresh, bounded module report."""

import json
from unittest.mock import patch

from tests.unit import test_maddy_modules
from tools.maddy_modules import aggregate_modules, module_inventory
from tools.run_linux import suite_command


class RuntimeModuleTests(test_maddy_modules.MaddyModuleTests):
    def setUp(self):
        super().setUp()
        self.inventory['suite'] = 'runtime'
        self.template['suite'] = 'runtime'
        self.template['controller_memory']['limit_bytes'] = 128 * 1024**2
        for name in self.inventory['modules']:
            self.write(name, {**self.template, 'module': name, 'tests': {name+'.case': 'pass'}})

    def write(self, name, report):
        (self.root / name / 'linux-runtime-checks.json').write_text(json.dumps(report))

    def test_all_modules_are_required_and_original_reports_remain_intact(self):
        before = {p: p.read_bytes() for p in self.root.rglob('linux-runtime-checks.json')}
        report = self.aggregate()
        self.assertEqual(report['result'], 'pass')
        self.assertEqual(report['suite'], 'runtime')
        self.assertEqual(report['controller_memory']['limit_bytes'], 128 * 1024**2)
        self.assertEqual(set(report['tests']), {'test_first.case', 'test_second.case'})
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_runtime_inventory_selects_only_the_requested_suite_and_rejects_mismatch(self):
        calls = []

        def docker(arguments):
            calls.append(arguments)
            return json.dumps(self.inventory)

        self.assertEqual(module_inventory('pinned-image', docker, 'runtime'), self.inventory)
        self.assertIn('MAIL_ADMIN_MODULE_SUITE=runtime', calls[0])
        with self.assertRaises(ValueError):
            module_inventory('pinned-image', docker, 'integration')
        with patch.dict(self.inventory, {'suite': 'maddy'}), self.assertRaises(ValueError):
            module_inventory('pinned-image', docker, 'runtime')

    def test_partial_runtime_selection_is_not_a_complete_suite_report(self):
        arguments = suite_command('runtime', None, 'test_mail_flow')
        self.assertEqual(arguments[:5], ('python', 'tools/run_tests.py', 'runtime',
                                         '--module', 'test_mail_flow'))
        self.assertTrue(arguments[-1].startswith('build/runtime-partial-'))
        self.assertTrue(arguments[-1].endswith('/test_mail_flow'))
        with self.assertRaises(ValueError):
            suite_command('runtime', self.root / 'evidence.json', 'test_mail_flow')

    def test_malformed_report_or_invalid_inventory_is_an_explicit_failure(self):
        self.write('test_second', {'tests': 'invalid'})
        self.assertEqual(aggregate_modules(self.root, (self.inventory, {}, 'start'))['result'],
                         'fail')
