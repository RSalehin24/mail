"""Fail-closed evidence qualification; never deploys or runs application tests."""

import argparse
import json
import pathlib
from datetime import datetime, timezone

from verification_support import (
    PACKAGE_ROOT, ValidationError, canonical_digest, file_digest, indexed,
    inside_file, parse_timestamp, read_json, require, specification_digest,
    valid_digest,
)


def load_context(file, now):
    evidence = read_json(file)
    require(isinstance(evidence, dict), 'Evidence must be an object')
    return {'root': file.resolve().parent, 'evidence': evidence,
            'contract': read_json(PACKAGE_ROOT / 'requirements/contracts.json'),
            'catalog': read_json(PACKAGE_ROOT / 'verification/test-catalog.json'),
            'now': now or datetime.now(timezone.utc)}


def validate_identity(context):
    evidence = context['evidence']
    expected = {'schema_version', 'release_id', 'specification_sha256', 'runner_revision',
                'artifact_set_sha256', 'artifacts', 'inputs', 'tests', 'gates', 'approval'}
    require(set(evidence) == expected, 'Unexpected or missing evidence fields')
    require(type(evidence['schema_version']) is int and evidence['schema_version'] == 1,
            'Unsupported evidence schema')
    require(isinstance(evidence['release_id'], str) and evidence['release_id'].strip(),
            'Missing release identity')
    require(evidence['specification_sha256'] == specification_digest(),
            'Evidence targets a different specification/tool version')
    require(valid_digest(evidence['runner_revision']), 'Missing runner revision')


def verified_file(context, record):
    allowed = ({'id', 'path', 'sha256'}, {'id', 'report', 'report_sha256'})
    require(set(record) in allowed, 'Ambiguous or unexpected file-reference fields')
    require(valid_digest(record.get('sha256', record.get('report_sha256'))),
            'Missing or invalid file digest')
    reference = record.get('path', record.get('report'))
    file = inside_file(context['root'], reference)
    expected = record.get('sha256', record.get('report_sha256'))
    require(file_digest(file) == expected, 'Artifact/report content digest mismatch')
    return file


def validate_artifacts(context):
    evidence = context['evidence']
    records = indexed(evidence['artifacts'])
    require(set(records) == set(context['contract']['required_artifacts']),
            'Missing or unexpected release artifact')
    require(all(set(record) == {'id', 'path', 'sha256'} for record in records.values()),
            'Invalid artifact reference fields')
    files = {name: verified_file(context, record) for name, record in records.items()}
    require(all(file.stat().st_size > 0 for file in files.values()), 'Empty artifact')
    identity = canonical_digest({name: record['sha256'] for name, record in records.items()})
    require(identity == evidence['artifact_set_sha256'], 'Artifact-set identity mismatch')
    require(records['qualification_suite']['sha256'] == evidence['runner_revision'],
            'Runner revision does not match the qualification suite artifact')
    configuration = read_json(files['deployment_config'])
    require(isinstance(configuration, dict), 'Invalid deployment configuration artifact')
    require(configuration.get('inputs') == evidence['inputs'], 'Configuration/input mismatch')


def validate_input_value(value, rule):
    kind = rule['type']
    if kind == 'positive_integer':
        return type(value) is int and value > 0
    if kind == 'boolean':
        return type(value) is bool
    if kind == 'choice':
        return value in rule['values']
    return isinstance(value, str) and bool(value.strip()) and '\x00' not in value


def validate_resources(inputs):
    require(inputs['tmpfs_limit_bytes'] <= inputs['memory_limit_bytes'],
            'Temporary storage exceeds container budget')
    require(inputs['memory_limit_bytes'] + inputs['reserved_host_mail_bytes'] <=
            inputs['host_memory_bytes'], 'Container and reserve exceed host memory')
    hash_peak = inputs['hash_memory_kib'] * 1024 * inputs['hash_parallelism']
    require(hash_peak <= inputs['memory_limit_bytes'], 'Hash work exceeds container budget')
    require(valid_digest(inputs['protected_policy_sha256']), 'Invalid recipient-policy digest')
    image = inputs['maddy_image_id']
    require(image.startswith('sha256:') and valid_digest(image[7:]), 'Invalid Maddy image ID')
    require(inputs['mx_mode'] != 'direct' or inputs['mx_cutover_approved'],
            'Direct MX requires explicit cutover approval')


def validate_inputs(context):
    inputs = context['evidence']['inputs']
    fields = context['contract']['input_fields']
    require(isinstance(inputs, dict) and set(inputs) == set(fields),
            'Missing or unknown deployment input')
    for name, rule in fields.items():
        require(validate_input_value(inputs[name], rule), 'Invalid deployment input: ' + name)
    validate_resources(inputs)


def validate_report_time(context, report):
    started = parse_timestamp(report.get('started_at_utc'))
    completed = parse_timestamp(report.get('completed_at_utc'))
    now = context['now']
    require(started <= completed <= now, 'Future or reversed report timestamps')
    age = (now - started).total_seconds()
    require(age <= context['contract']['evidence_max_age_seconds'], 'Stale evidence')
    return completed


def validate_report_identity(context, report, group):
    require(type(report.get('schema_version')) is int and report['schema_version'] == 1,
            'Unsupported report schema')
    require(report.get('test_id') == group['id'], 'Report/group identity mismatch')
    require(report.get('environment') == group['environment'], 'Wrong test environment')
    for field in ('artifact_set_sha256', 'specification_sha256', 'runner_revision'):
        require(report.get(field) == context['evidence'][field], 'Report binding mismatch: ' + field)
    require(report.get('result') == 'pass', 'A report did not pass')


def validate_report(context, record, group):
    report = read_json(verified_file(context, record))
    require(isinstance(report, dict), 'Report must be an object')
    validate_report_identity(context, report, group)
    cases = indexed(report.get('cases'))
    expected = {case['id'] for case in group['cases']}
    require(set(cases) == expected, 'Missing, unexpected or duplicate case coverage')
    require(all(case.get('result') == 'pass' for case in cases.values()),
            'Failed or skipped case')
    return validate_report_time(context, report)


def required_tests(context, phase):
    groups = context['catalog']['groups']
    required = {group['id'] for group in groups if group['id'].startswith('T')}
    if phase != 'release':
        return required
    conditions = context['contract']['conditional_tests']
    for group in groups:
        condition = conditions.get(group['id'])
        enabled = condition is None or context['evidence']['inputs'][condition['field']] == condition['equals']
        if enabled:
            required.add(group['id'])
    return required


def validate_collection(context, records, groups):
    expected, required = groups
    by_id = indexed(records)
    require(set(by_id) <= set(expected), 'Unknown evidence group')
    require(required <= set(by_id), 'Missing required evidence group')
    completed = []
    for identifier, record in by_id.items():
        require(set(record) == {'id', 'report', 'report_sha256'}, 'Invalid report reference fields')
        provided = record.get('report') is not None
        require(provided or identifier not in required, 'Required group has no report: ' + identifier)
        if provided:
            completed.append(validate_report(context, record, expected[identifier]))
    return completed


def validate_all_reports(context, phase):
    test_groups = {group['id']: group for group in context['catalog']['groups']}
    test_rules = (test_groups, required_tests(context, phase))
    completed = validate_collection(context, context['evidence']['tests'], test_rules)
    gates = {group['id']: group for group in context['contract']['gates']}
    required = set(gates) if phase == 'release' else ({'G01', 'G02'} if phase == 'preflight' else set())
    gate_rules = (gates, required)
    completed += validate_collection(context, context['evidence']['gates'], gate_rules)
    validate_gate_order(context, required)
    return completed


def gate_reports(context, identifiers):
    records = indexed(context['evidence']['gates'])
    return {identifier: read_json(verified_file(context, records[identifier]))
            for identifier in identifiers}


def validate_gate_order(context, required):
    reports = gate_reports(context, required)
    gates = indexed(context['contract']['gates'])
    for identifier, report in reports.items():
        for parent in gates[identifier]['depends_on']:
            require(parent in reports, 'Missing prerequisite gate')
            require(parse_timestamp(reports[parent]['completed_at_utc']) <=
                    parse_timestamp(report['started_at_utc']), 'Gate chronology violates dependencies')
    if 'G01' in reports:
        validate_candidate_timing(context, reports['G01'])
    if 'G08' in reports:
        validate_target_timing(context, reports)


def validate_candidate_timing(context, gate):
    records = context['evidence']['tests']
    reports = [read_json(verified_file(context, record)) for record in records
               if record['id'].startswith('T')]
    completed = max(parse_timestamp(report['completed_at_utc']) for report in reports)
    require(completed <= parse_timestamp(gate['completed_at_utc']),
            'Candidate gate predates its test evidence')


def validate_target_timing(context, gates):
    required = required_tests(context, 'release')
    records = context['evidence']['tests']
    reports = [read_json(verified_file(context, record)) for record in records
               if record['id'].startswith('P') and record['id'] in required]
    first = min(parse_timestamp(report['started_at_utc']) for report in reports)
    last = max(parse_timestamp(report['completed_at_utc']) for report in reports)
    require(parse_timestamp(gates['G07']['completed_at_utc']) <= first,
            'Target evidence predates application startup')
    require(last <= parse_timestamp(gates['G08']['completed_at_utc']),
            'Acceptance gate predates target evidence')


def validate_approval(context, completed):
    approval = context['evidence']['approval']
    require(isinstance(approval, dict), 'Missing final operator approval')
    require(isinstance(approval.get('approved_by'), str) and approval['approved_by'].strip(),
            'Missing approving operator')
    require(approval.get('artifact_set_sha256') == context['evidence']['artifact_set_sha256'],
            'Approval targets a different artifact set')
    approved = parse_timestamp(approval.get('approved_at_utc'))
    require(max(completed) <= approved <= context['now'], 'Approval precedes evidence or is future-dated')


def verify_evidence(file, phase='release', now=None):
    require(phase in {'candidate', 'preflight', 'release'}, 'Unknown qualification phase')
    context = load_context(pathlib.Path(file), now)
    validate_identity(context)
    validate_artifacts(context)
    if phase != 'candidate':
        validate_inputs(context)
    completed = validate_all_reports(context, phase)
    if phase == 'release':
        validate_approval(context, completed)
    return {'phase': phase, 'result': 'pass', 'release_id': context['evidence']['release_id'],
            'scope': 'trusted-runner evidence validation; no deployment or application execution'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=pathlib.Path)
    parser.add_argument('--phase', choices=('candidate', 'preflight', 'release'), default='release')
    arguments = parser.parse_args()
    try:
        result = verify_evidence(arguments.evidence, arguments.phase)
    except (ValidationError, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'result': 'blocked', 'reason': str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
