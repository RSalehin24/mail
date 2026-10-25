"""Execute the actual gate validator against failed, missing and misordered inputs."""

import hashlib
import json
import os
import unittest
from datetime import UTC, datetime

from host.rollout import RolloutGuard, RolloutPaths
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.rollout_support import write_gate_fixtures
from tests.support import StateFixture
from tools.qualification_bundle import ROOT


class RolloutGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root
        self.deployment = {
            'artifact_set_sha256': 'a' * 64, 'specification_sha256': 'b' * 64,
            'qualification_suite_sha256': 'c' * 64,
            'inputs': {'target_instance_id': 'synthetic-target',
                       'target_ssh_host_key_sha256': 'd' * 64},
        }
        write_gate_fixtures(self.root, self.deployment, self.fixture.now)
        self.guard = RolloutGuard(self.deployment, (
            RolloutPaths(directory=self.root), os.getuid(), lambda: self.fixture.now,
        ))

    def change_report(self, identifier, change):
        path = self.root / 'reports' / (identifier + '.json')
        record = protected_json(path, os.getuid())
        record.update(change)
        atomic_json(path, record)
        references = protected_json(self.root / 'checkpoints.json', os.getuid())
        references[identifier]['report_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(self.root / 'checkpoints.json', references)

    def assert_maintenance_closed(self):
        for identifier in ('G02', 'G03', 'G04', 'G05', 'G06', 'G07', 'G08'):
            with self.subTest(dependent=identifier), self.assertRaises(ServiceError):
                self.guard.checkpoint(identifier)

    def test_failed_build_or_recovery_assertion_blocks_all_dependent_maintenance_gates(self):
        original = protected_json(self.root / 'reports/G01.json', os.getuid())
        for case in original['cases']:
            failed = [{**row, 'result': 'fail' if row['id'] == case['id'] else 'pass'}
                      for row in original['cases']]
            self.change_report('G01', {'result': 'blocked', 'cases': failed})
            self.assert_maintenance_closed()
        self.change_report('G01', original)
        self.guard.checkpoint('G08')

    def test_every_missing_or_invalid_deployment_input_is_rejected(self):
        from check_release import validate_inputs

        contract = json.loads((ROOT / 'docs/mail-admin-implementation/requirements/'
                               'contracts.json').read_bytes())
        template = json.loads((ROOT / 'docs/mail-admin-implementation/verification/'
                               'runtime-evidence.template.json').read_bytes())
        for name in contract['input_fields']:
            inputs = {**template['inputs']}
            inputs.pop(name)
            with self.subTest(input=name), self.assertRaises(ValueError):
                validate_inputs({'evidence': {'inputs': inputs}, 'contract': contract})
        with self.assertRaises(ValueError):
            validate_inputs({'evidence': template, 'contract': contract})

    def test_skipped_failed_missing_and_reversed_phase_gates_cannot_be_used(self):
        for identifier in ('G01', 'G02', 'G03', 'G04', 'G05', 'G06', 'G07', 'G08'):
            path = self.root / 'reports' / (identifier + '.json')
            original = protected_json(path, os.getuid())
            for outcome in ('skipped', 'blocked', 'fail'):
                self.change_report(identifier, {'result': outcome})
                with (self.subTest(gate=identifier, outcome=outcome),
                      self.assertRaises(ServiceError)):
                    self.guard.checkpoint('G08')
            self.change_report(identifier, original)
            path.rename(path.with_suffix('.retained'))
            with self.assertRaises(FileNotFoundError):
                self.guard.checkpoint('G08')
            path.with_suffix('.retained').rename(path)
        self.change_report('G02', {'started_at_utc': datetime.fromtimestamp(
            self.fixture.now - 2000, UTC,
        ).isoformat()})
        with self.assertRaisesRegex(ServiceError, 'checkpoint_order_invalid'):
            self.guard.checkpoint('G08')
