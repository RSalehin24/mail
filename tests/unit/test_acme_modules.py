"""ACME uses the same original bounded, complete-module contract as runtime tests."""

import json
from unittest.mock import patch

from tests.unit import test_maddy_modules
from tools.maddy_modules import module_inventory
from tools.run_linux import suite_command


class AcmeModuleTests(test_maddy_modules.MaddyModuleTests):
    def setUp(self):
        super().setUp()
        self.inventory['suite'] = 'acme'
        self.template['suite'] = 'acme'
        for name in self.inventory['modules']:
            self.write(name, {**self.template, 'module': name, 'tests': {name+'.case': 'pass'}})

    def write(self, name, report):
        (self.root / name / 'linux-acme-checks.json').write_text(json.dumps(report))

    def test_all_modules_are_required_and_original_reports_remain_intact(self):
        before = {p: p.read_bytes() for p in self.root.rglob('linux-acme-checks.json')}
        report = self.aggregate()
        self.assertEqual(report['result'], 'pass')
        self.assertEqual(report['suite'], 'acme')
        self.assertEqual(report['controller_memory']['limit_bytes'], 192 * 1024**2)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_inventory_is_bound_to_acme_and_partial_selection_cannot_accept_evidence(self):
        self.assertEqual(module_inventory('image', lambda _: json.dumps(self.inventory), 'acme'),
                         self.inventory)
        with patch.dict(self.inventory, {'suite': 'runtime'}), self.assertRaises(ValueError):
            module_inventory('image', lambda _: json.dumps(self.inventory), 'acme')
        arguments = suite_command('acme', None, 'test_generation_crashes')
        self.assertEqual(arguments[:5], ('python', 'tools/run_tests.py', 'acme',
                                         '--module', 'test_generation_crashes'))
        with self.assertRaises(ValueError):
            suite_command('acme', self.root / 'evidence.json', 'test_generation_crashes')
