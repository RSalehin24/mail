"""Synthetic metadata fixtures; these are never application qualification evidence."""

import copy
import json
from datetime import timedelta

from verification_support import (
    PACKAGE_ROOT, canonical_digest, file_digest, read_json, specification_digest,
)


def fixture_inputs(contract):
    values = {name: ('synthetic-operator-reference' if rule['type'] == 'string' else 1)
              for name, rule in contract['input_fields'].items()}
    values.update({'email_otp_enabled': False, 'permanent_deletion_enabled': False,
                   'mx_cutover_approved': False, 'mx_mode': 'cloudflare',
                   'protected_policy_sha256': 'a' * 64, 'maddy_image_id': 'sha256:' + 'b' * 64,
                   'target_ssh_host_key_sha256': 'c' * 64,
                   'memory_limit_bytes': 256 * 1024 ** 2, 'tmpfs_limit_bytes': 16 * 1024 ** 2,
                   'host_memory_bytes': 1024 ** 3, 'reserved_host_mail_bytes': 512 * 1024 ** 2,
                   'hash_memory_kib': 8192, 'service_uid': 10001, 'service_gid': 10001})
    return values


class EvidenceFixture:
    def __init__(self, root, now):
        self.root = root
        self.now = now
        self.contract = read_json(PACKAGE_ROOT / 'requirements/contracts.json')
        self.catalogue = read_json(PACKAGE_ROOT / 'verification/test-catalog.json')
        self.document = read_json(PACKAGE_ROOT / 'verification/runtime-evidence.template.json')
        self.inputs = fixture_inputs(self.contract)
        (root / 'artifacts').mkdir()
        (root / 'reports').mkdir()
        self.refresh()

    def save(self):
        file = self.root / 'evidence.json'
        file.write_text(json.dumps(self.document, indent=2) + '\n')
        return file

    def refresh(self):
        self.document['release_id'] = 'synthetic-unit-fixture'
        self.document['specification_sha256'] = specification_digest()
        self.document['inputs'] = copy.deepcopy(self.inputs)
        self.write_artifacts()
        self.write_reports()
        self.document['approval'] = {
            'approved_by': 'synthetic-unit-test-operator',
            'approved_at_utc': (self.now - timedelta(seconds=1)).isoformat(),
            'artifact_set_sha256': self.document['artifact_set_sha256']}
        return self.save()

    def write_artifacts(self):
        for record in self.document['artifacts']:
            file = self.root / 'artifacts' / (record['id'] + '.json')
            content = {'inputs': self.inputs} if record['id'] == 'deployment_config' else {'synthetic': record['id']}
            file.write_text(json.dumps(content) + '\n')
            record.update({'path': str(file.relative_to(self.root)), 'sha256': file_digest(file)})
        records = {record['id']: record['sha256'] for record in self.document['artifacts']}
        self.document['artifact_set_sha256'] = canonical_digest(records)
        self.document['runner_revision'] = records['qualification_suite']

    def report_times(self, identifier):
        if identifier.startswith('G'):
            start = self.now - timedelta(minutes=60 - 2 * int(identifier[1:]))
            return start, start + timedelta(minutes=1)
        if identifier.startswith('P'):
            return self.now - timedelta(seconds=2630), self.now - timedelta(seconds=2590)
        return self.now - timedelta(hours=3), self.now - timedelta(hours=2)

    def replace_configuration_inputs(self, inputs):
        record = next(record for record in self.document['artifacts'] if record['id'] == 'deployment_config')
        file = self.root / record['path']
        file.write_text(json.dumps({'inputs': inputs}) + '\n')
        record['sha256'] = file_digest(file)
        identities = {record['id']: record['sha256'] for record in self.document['artifacts']}
        self.document['artifact_set_sha256'] = canonical_digest(identities)
        self.write_reports()
        self.document['approval']['artifact_set_sha256'] = self.document['artifact_set_sha256']

    def make_report(self, group):
        started, completed = self.report_times(group['id'])
        report = {'schema_version': 1, 'test_id': group['id'], 'result': 'pass',
                'artifact_set_sha256': self.document['artifact_set_sha256'],
                'specification_sha256': self.document['specification_sha256'],
                'runner_revision': self.document['runner_revision'],
                'environment': group['environment'], 'started_at_utc': started.isoformat(),
                'completed_at_utc': completed.isoformat(),
                'cases': [{'id': case['id'], 'result': 'pass'} for case in group['cases']]}
        self.bind_target(report)
        return report

    def bind_target(self, report):
        if report['environment'] not in {'target-production', 'operator-preflight'}:
            return
        report['observed_target'] = {name: self.inputs[name]
                                    for name in ('target_instance_id', 'target_ssh_host_key_sha256')}
        if report['test_id'].startswith('P'):
            identities = {record['id']: record['sha256'] for record in self.document['artifacts']}
            report['observed_deployment'] = {name: identities[name]
                                            for name in ('dashboard_image', 'host_bundle', 'deployment_config')}
            report['observed_deployment']['maddy_image_id'] = self.inputs['maddy_image_id']

    def write_reports(self):
        groups = {group['id']: group for group in self.catalogue['groups'] + self.contract['gates']}
        records = self.document['tests'] + self.document['gates']
        for record in records:
            if record['id'] in {'P06', 'P07', 'P08'}:
                record.update({'report': None, 'report_sha256': None})
                continue
            self.save_report(record['id'], self.make_report(groups[record['id']]))

    def record(self, identifier):
        records = self.document['tests'] + self.document['gates']
        return next(record for record in records if record['id'] == identifier)

    def report(self, identifier):
        record = self.record(identifier)
        return read_json(self.root / record['report'])

    def save_report(self, identifier, report):
        file = self.root / 'reports' / (identifier + '.json')
        file.write_text(json.dumps(report) + '\n')
        self.record(identifier).update({'report': str(file.relative_to(self.root)),
                                        'report_sha256': file_digest(file)})
