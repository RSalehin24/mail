"""Validate complete group output before assembling a frozen catalogue result."""

import os
import re
from pathlib import Path

from tools.qualification_cases import CASE_TESTS, HOST_CASE_TESTS


def report_directory(evidence: Path, selected: Path) -> Path:
    directory = selected.resolve()
    if directory.parent != evidence.resolve().parent or re.fullmatch(
        r'qualification-[0-9a-f-]{36}', directory.name
    ) is None:
        raise ValueError('Group reports must remain in their own frozen bundle directory')
    if not directory.is_dir() or selected.is_symlink():
        raise ValueError('The group report directory must already exist without a symlink')
    return directory


def validate_group(bundle, group, report):
    validate_identity(bundle, group, report)
    cases = report.get('cases')
    if not isinstance(cases, list) or [case['id'] for case in cases] != [
        case['id'] for case in group['cases']
    ]:
        raise ValueError('Missing, repeated or reordered catalogue cases')
    for case in cases:
        validate_case(case, report.get('execution', {}))
    validate_outcome(report)
    validate_execution(report['execution'])


def validate_identity(bundle, group, report):
    if (type(report.get('schema_version')) is not int or report['schema_version'] != 1
            or report.get('test_id') != group['id']
            or report.get('environment') != group['environment']
            or any(report.get(key) != value for key, value in bundle.identity.items())):
        raise ValueError('Group report differs from the frozen catalogue identity')


def validate_outcome(report):
    expected = 'pass' if all(case['result'] == 'pass' for case in report['cases']) else 'blocked'
    if report.get('result') != expected:
        raise ValueError('Group outcome differs from actual case outcomes')


def validate_case(case, execution):
    expected = CASE_TESTS.get(case['id'], ())
    host = HOST_CASE_TESTS.get(case['id'], ())
    if host and not execution.get('host'):
        if (expected and case['result'] == 'fail'
                and isinstance(case['assertions'], dict)
                and set(case['assertions']) == set(expected)
                and any(value != 'pass' for value in case['assertions'].values())):
            return
        if case != {'id': case['id'], 'result': 'not_run', 'assertions': []}:
            raise ValueError('Missing host observations cannot qualify a composite case')
        return
    expected += host
    if case['result'] == 'not_run':
        if expected or case['assertions'] != []:
            raise ValueError('A registered assertion cannot silently become not_run')
        return
    if case['result'] == 'fail':
        return
    if (case['result'] != 'pass' or not expected
            or case['assertions'] != dict.fromkeys(expected, 'pass')):
        raise ValueError('Missing or failed assertions cannot become a passing case')


def validate_execution(execution, images=None):
    if (execution.get('system') != 'Linux'
            or execution.get('machine') not in {'x86_64', 'amd64'}):
        raise ValueError('Group execution must observe isolated Linux/amd64')
    for key, variable in (('runtime_image', 'MAIL_ADMIN_RUNTIME_IMAGE'),
                          ('qualification_image', 'MAIL_ADMIN_QUALIFICATION_IMAGE')):
        expected = os.environ[variable] if images is None else images[key]
        if execution.get(key) != expected:
            raise ValueError('A group used a different image from the frozen execution')
