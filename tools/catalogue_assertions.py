"""Assemble catalogue cases only from exact, observed frozen assertion-module runs."""

import copy
import hashlib
import json
import re

from tools.catalogue_reports import validate_execution, validate_identity, validate_outcome
from tools.qualification_bundle import validate_report_time
from tools.qualification_cases import CASE_TESTS
from tools.run_tests import controller_memory, timestamp

MODULE = r'tests\.(?:unit|integration|maddy|runtime|acme|browser)\.test_[a-z0-9_]+'


def assertion_modules(mapping=None):
    selected = CASE_TESTS if mapping is None else mapping
    modules = {}
    for identifier in sorted({test for tests in selected.values() for test in tests}):
        module = identifier.rsplit('.', 2)[0]
        if re.fullmatch(MODULE + r'\.[A-Za-z0-9_]+\.test_[a-z0-9_]+', identifier) is None:
            raise ValueError('Invalid registered qualification assertion')
        modules.setdefault(module, []).append(identifier)
    return modules


def validate_module_inventory(inventory):
    modules = inventory.get('assertion_modules')
    if not isinstance(modules, dict) or not modules:
        raise ValueError('A complete assertion-module inventory is required')
    for module, tests in modules.items():
        if (not isinstance(module, str) or re.fullmatch(MODULE, module) is None
                or not isinstance(tests, list) or not tests or len(set(tests)) != len(tests)
                or any(not isinstance(test, str) or not test.startswith(module + '.')
                       or re.fullmatch(MODULE + r'\.[A-Za-z0-9_]+\.test_[a-z0-9_]+', test) is None
                       for test in tests)):
            raise ValueError('Invalid qualification module inventory')
    return modules


def module_group(module, tests):
    return {'id': module, 'environment': 'isolated-linux-amd64',
            'cases': [{'id': test} for test in tests]}


def validate_module_report(bundle, selection, report):
    module, tests, *images = selection
    validate_identity(bundle, module_group(module, tests), report)
    validate_report_time(bundle.context, report)
    cases = report.get('cases')
    if not isinstance(cases, list) or [case['id'] for case in cases] != tests:
        raise ValueError('Missing, repeated or reordered module assertions')
    for case in cases:
        outcomes = case.get('assertions')
        if not isinstance(outcomes, dict) or set(outcomes) != {case['id']}:
            raise ValueError('Missing or foreign module assertion outcome')
        outcome = outcomes[case['id']]
        if outcome not in {'pass', 'fail', 'error', 'skipped', 'expected_failure',
                           'unexpected_success'} or case['result'] != (
            'pass' if outcome == 'pass' else 'fail'
        ):
            raise ValueError('Module assertion result differs from its actual outcome')
    validate_outcome(report)
    validate_execution(report['execution'], images[0] if images else None)
    memory = report['execution'].get('controller_memory', {})
    if memory.get('state') != 'observed' or memory.get('limit_bytes') != 192 * 1024**2:
        raise ValueError('The original module controller bound must be observed')


def validate_controller(module, records, report):
    if len(records) != 2 or records[0].get('group') != module:
        raise ValueError('Missing or incomplete assertion-controller observation')
    first, last = records
    if (re.fullmatch(r'[0-9a-f]{64}', first.get('container_id', '')) is None
            or re.fullmatch(r'mail-admin-catalogue-controller-[0-9a-f-]{36}',
                            first.get('container_name', '')) is None):
        raise ValueError('Invalid assertion-controller identity')
    expected = 0 if report['result'] == 'pass' else 1
    state = last.get('state', {})
    if (state.get('Running') is not False or state.get('OOMKilled') is not False
            or type(state.get('ExitCode')) is not int or state['ExitCode'] != expected
            or type(last.get('attached_exit_code')) is not int
            or last['attached_exit_code'] != expected):
        raise ValueError('Killed, surviving or mismatched controllers cannot qualify assertions')


def read_modules(bundle, directory):
    reports = {}
    for module, tests in assertion_modules().items():
        path = directory / (module + '.json')
        progress = directory / (module + '-controller.jsonl')
        if path.is_symlink() or progress.is_symlink():
            raise ValueError('Symlinked qualification observations are not accepted')
        report = json.loads(path.read_bytes())
        validate_module_report(bundle, (module, tests), report)
        observations = [json.loads(line) for line in progress.read_text().splitlines()]
        validate_controller(module, observations, report)
        reports[module] = report
    return reports


def case_from_assertions(identifier, outcomes):
    tests = CASE_TESTS.get(identifier, ())
    if not tests:
        return {'id': identifier, 'result': 'not_run', 'assertions': []}
    actual = {test: outcomes[test] for test in tests}
    return {'id': identifier, 'result': 'pass' if all(value == 'pass'
                                                    for value in actual.values()) else 'fail',
            'assertions': actual}


def group_from_modules(bundle, group, context):
    directory, reports = context
    tests = {test for case in group['cases'] for test in CASE_TESTS.get(case['id'], ())}
    modules = sorted({test.rsplit('.', 2)[0] for test in tests})
    selected = [reports[module] for module in modules]
    outcomes = {case['id']: case['assertions'][case['id']]
                for report in selected for case in report['cases']}
    cases = [case_from_assertions(case['id'], outcomes) for case in group['cases']]
    execution = group_execution(selected)
    execution['assertion_modules'] = {
        module: {'report': module + '.json',
                 'sha256': hashlib.sha256(
                     (directory / (module + '.json')).read_bytes()).hexdigest()}
        for module in modules
    }
    return {'schema_version': 1, 'test_id': group['id'], **bundle.identity,
            'environment': group['environment'],
            'started_at_utc': min((report['started_at_utc'] for report in selected),
                                  default=timestamp()),
            'completed_at_utc': timestamp(), 'cases': cases,
            'result': 'pass' if all(case['result'] == 'pass' for case in cases) else 'blocked',
            'execution': execution}


def group_execution(reports):
    import os
    import platform

    if not reports:
        return {'system': platform.system(), 'machine': platform.machine(),
                'runtime_image': os.environ['MAIL_ADMIN_RUNTIME_IMAGE'],
                'qualification_image': os.environ['MAIL_ADMIN_QUALIFICATION_IMAGE'],
                'controller_memory': controller_memory()}
    execution = copy.deepcopy(reports[0]['execution'])
    memory = execution['controller_memory']
    memory['peak_bytes'] = max(report['execution']['controller_memory']['peak_bytes']
                               for report in reports)
    memory.pop('current_bytes', None)
    memory['scope'] = 'maximum among sequential assertion-module controllers; not whole host'
    return execution
