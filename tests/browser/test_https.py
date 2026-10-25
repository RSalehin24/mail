"""Check separately executed Chrome assertions against the same frozen HTTPS candidate."""

import hashlib
import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path

from tools.browser_inputs import pinned_inputs
from tools.qualification_bundle import QualificationBundle, validate_report_time
from tools.run_tests import source_digest


def browser_observation():
    bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_CATALOGUE_EVIDENCE']))
    bundle.verify_source()
    root = bundle.context['root']
    reference = json.loads((root / 'browser-observation-reference.json').read_bytes())
    relative = reference['report']
    if re.fullmatch(r'https-browser-[0-9a-f-]{36}/observation\.json', relative) is None:
        raise ValueError('Invalid frozen browser observation path')
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Untrusted browser observation path')
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != reference['sha256']:
        raise ValueError('Browser observation changed')
    report = json.loads(content)
    validate_browser_report(report, bundle)
    return report


def validate_browser_report(report, bundle):
    expected = {'mail-admin:candidate': os.environ['MAIL_ADMIN_RUNTIME_IMAGE'],
                'mail-admin:qualification': os.environ['MAIL_ADMIN_QUALIFICATION_IMAGE']}
    if (report.get('scope') != 'frozen-https-browser' or report.get('identity') != bundle.identity
            or report.get('images') != expected or report.get('source_sha256') != source_digest()
            or report.get('runner_inputs') != pinned_inputs(report.get('browser_system', ''))
            or report.get('result') != 'pass' or report.get('browser', {}).get('result') != 'pass'):
        raise ValueError('Browser assertions do not match the frozen execution')
    completed = validate_report_time(bundle.context, report)
    browser_completed = validate_report_time(bundle.context, report['browser'])
    root_started = datetime.fromisoformat(report['started_at_utc'].replace('Z', '+00:00'))
    value = report['browser']['started_at_utc'].replace('Z', '+00:00')
    browser_started = datetime.fromisoformat(value)
    if not root_started <= browser_started <= browser_completed <= completed:
        raise ValueError('Browser execution is outside its observed fixture interval')


class HttpsBrowserTests(unittest.TestCase):
    def test_secrets_are_masked_no_store_and_absent_from_browser_and_application_logs(self):
        report = browser_observation()
        expected = ('password_fields_masked', 'session_survives_reload',
                    'reveal_requires_recent_auth', 'http_only_cookie', 'no_store_api',
                    'secret_browser_persistence_absent', 'isolated_dns_unavailable')
        self.assertTrue(all(report['browser']['assertions'].get(key) is True for key in expected))
        self.assertIs(report['fixture']['secret_logs_absent'], True)
        self.assertEqual(report['fixture']['dns_servers'], ['127.0.0.1'])
        self.assertGreaterEqual(report['browser']['api_responses_observed'], 10)

    def test_untrusted_names_and_notes_are_inert_and_actual_api_errors_have_only_safe_ids(self):
        report = browser_observation()
        for key in ('untrusted_text_inert', 'errors_have_safe_ids'):
            self.assertIs(report['browser']['assertions'].get(key), True)

    def test_fresh_bootstrap_precedes_startup_and_enrollment_follows_lock_release(self):
        report = browser_observation()
        sequence = report['fixture']['bootstrap_sequence']
        self.assertEqual(len(sequence), 5)
        self.assertEqual(sorted(sequence), sequence)
        self.assertLess(sequence[1], sequence[2])
        self.assertLess(sequence[2], sequence[3])
        self.assertLess(sequence[3], sequence[4])
        calls = report['fixture']['api_call_times']
        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(timestamp >= sequence[3] for timestamp in calls))
