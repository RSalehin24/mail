"""A resumed run must retain failures and reject every frozen-identity change."""

import copy
import json

from tests.unit.test_catalogue_assertions import CatalogueAssertionFixture
from tools.catalogue_resume import (
    bind_directory,
    completed_module,
    execution_identity,
    preserve_assembly,
    require_stopped_controller,
    resume_directory,
)


class CatalogueResumeTests(CatalogueAssertionFixture):
    def setUp(self):
        super().setUp()
        self.root = (self.root / 'catalogue').resolve()
        self.root.mkdir()
        self.identity = {
            'inventory': {**self.bundle.identity, 'assertion_modules': self.modules,
                          'source_sha256': 'f' * 64},
            'images': {'runtime_image': 'runtime', 'qualification_image': 'suite'},
            'controller': {'--memory': '192m'},
            'engine': {'ID': 'engine', 'OSType': 'linux', 'Architecture': 'aarch64'},
        }

    def save_module(self):
        (self.root / (self.module + '.json')).write_text(json.dumps(self.report))
        (self.root / (self.module + '-controller.jsonl')).write_text(
            '\n'.join(json.dumps(row) for row in self.records) + '\n',
        )

    def completed(self):
        return completed_module(self.root, (self.module, [self.test]), (self.identity, self.bundle))

    def test_only_matching_passes_are_reused_without_rewriting_the_original_evidence(self):
        bind_directory(self.root, self.identity)
        self.save_module()
        before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in self.root.iterdir()}
        bind_directory(self.root, self.identity)
        self.assertTrue(self.completed())
        self.assertEqual(before, {
            p.name: (p.stat().st_ino, p.read_bytes()) for p in self.root.iterdir()
        })

    def test_source_artifact_runner_image_inventory_engine_and_limits_cannot_drift(self):
        bind_directory(self.root, self.identity)
        changes = (
            ('inventory', 'source_sha256'), ('inventory', 'artifact_set_sha256'),
            ('inventory', 'specification_sha256'), ('inventory', 'runner_revision'),
            ('inventory', 'assertion_modules'), ('images', 'runtime_image'),
            ('images', 'qualification_image'), ('engine', 'ID'),
            ('engine', 'Architecture'), ('controller', '--memory'),
        )
        for section, key in changes:
            changed = copy.deepcopy(self.identity)
            changed[section][key] = 'changed'
            with self.subTest(section=section, key=key), self.assertRaisesRegex(
                ValueError, 'execution changed',
            ):
                bind_directory(self.root, changed)

    def test_failed_skipped_incomplete_and_oom_attempts_are_preserved_before_retry(self):
        for outcome in ('fail', 'skipped', 'expected_failure', 'error'):
            self.report['result'] = 'blocked'
            self.report['cases'][0].update(result='fail', assertions={self.test: outcome})
            self.records[-1].update(
                state={'Running': False, 'OOMKilled': False, 'ExitCode': 1}, attached_exit_code=1,
            )
            self.save_module()
            self.assertFalse(self.completed())
        self.report['result'] = 'pass'
        self.report['cases'][0].update(result='pass', assertions={self.test: 'pass'})
        self.records[-1]['state']['OOMKilled'] = True
        self.save_module()
        self.assertFalse(self.completed())
        self.assertEqual(len(list((self.root / 'attempts').rglob(self.module + '.json'))), 5)
        self.save_module()
        (self.root / (self.module + '.json')).rename(self.root / 'interrupted-report.json')
        self.assertFalse(self.completed())
        self.assertEqual(len(list((self.root / 'attempts').rglob('*-controller.jsonl'))), 6)

    def test_surviving_controller_blocks_a_retry_and_is_never_removed(self):
        self.save_module()
        calls = []

        def docker(arguments):
            calls.append(arguments)
            if arguments[0] == 'ps':
                return self.records[0]['container_id']
            return json.dumps([{'State': {'Running': True}}])

        with self.assertRaisesRegex(ValueError, 'surviving controller'):
            require_stopped_controller(self.root, self.module, docker)
        self.assertEqual([call[0] for call in calls], ['ps', 'inspect'])
        self.assertTrue((self.root / (self.module + '.json')).exists())

    def test_unbound_finished_foreign_and_symlinked_directories_cannot_resume(self):
        self.save_module()
        with self.assertRaisesRegex(ValueError, 'unbound'):
            bind_directory(self.root, self.identity)
        directory = self.root / 'qualification-current'
        directory.mkdir()
        evidence = self.root / 'evidence.json'
        self.assertEqual(resume_directory(evidence, directory), directory)
        (directory / 'result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'completed catalogue'):
            resume_directory(evidence, directory)
        with self.assertRaises(ValueError):
            resume_directory(evidence, self.root)

    def test_execution_binding_records_the_actual_controller_and_image_inputs(self):
        command = ('run', '--memory', '192m', '--memory-swap', '192m', '--cpus', '0.5',
                   '--pids-limit', '128', '--network', 'host', '-e',
                   'MAIL_ADMIN_RUNTIME_IMAGE=runtime', '-e',
                   'MAIL_ADMIN_QUALIFICATION_IMAGE=suite', 'suite')
        identity = execution_identity(self.identity['inventory'], command, self.identity['engine'])
        self.assertEqual(identity['images'], self.identity['images'])
        self.assertEqual(identity['controller']['--memory-swap'], '192m')
        self.assertEqual(identity['engine'], self.identity['engine'])

    def test_stale_future_reversed_and_missing_execution_times_are_rerun(self):
        changes = ({'started_at_utc': '2026-10-20T00:00:00+00:00'},
                   {'completed_at_utc': '2026-10-25T00:00:00+00:00'},
                   {'completed_at_utc': '2026-10-23T00:00:00+00:00'},
                   {'started_at_utc': None})
        for change in changes:
            original = copy.deepcopy(self.report)
            self.report.update(change)
            self.save_module()
            self.assertFalse(self.completed())
            self.report = original

    def test_interrupted_assembly_is_retained_without_repeating_verified_assertions(self):
        self.save_module()
        group = self.root / 'T01.json'
        group.write_bytes(b'original partial group')
        evidence = self.root.parent / (self.root.name + '-evidence.json')
        evidence.write_bytes(b'original interrupted evidence')
        preserve_assembly(self.root)
        self.assertFalse(group.exists())
        self.assertFalse(evidence.exists())
        retained = next((self.root / 'attempts').iterdir())
        self.assertEqual((retained / group.name).read_bytes(), b'original partial group')
        self.assertEqual((retained / evidence.name).read_bytes(), b'original interrupted evidence')
        self.assertTrue(self.completed())
