"""Development, stale and differently bound browser reports cannot qualify a candidate."""

import copy
import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tests.browser.test_https import validate_browser_report
from tools.browser_inputs import pinned_inputs


class BrowserObservationTests(unittest.TestCase):
    def setUp(self):
        self.bundle = SimpleNamespace(identity={'artifact_set_sha256': 'a' * 64}, context={
            'now': datetime(2026, 10, 24, 0, 2, tzinfo=UTC),
            'contract': {'evidence_max_age_seconds': 86400},
        })
        self.report = {
            'scope': 'frozen-https-browser', 'identity': self.bundle.identity,
            'images': {'mail-admin:candidate': 'runtime', 'mail-admin:qualification': 'suite'},
            'source_sha256': 'b' * 64, 'result': 'pass',
            'runner_inputs': pinned_inputs(),
            'started_at_utc': '2026-10-24T00:00:00+00:00',
            'completed_at_utc': '2026-10-24T00:01:00+00:00',
            'browser': {'result': 'pass', 'started_at_utc': '2026-10-24T00:00:10Z',
                        'completed_at_utc': '2026-10-24T00:00:50Z'},
        }
        boundary = patch.dict(os.environ, {'MAIL_ADMIN_RUNTIME_IMAGE': 'runtime',
                                         'MAIL_ADMIN_QUALIFICATION_IMAGE': 'suite'})
        boundary.start()
        self.addCleanup(boundary.stop)
        digest = patch('tests.browser.test_https.source_digest', return_value='b' * 64)
        digest.start()
        self.addCleanup(digest.stop)

    def test_matching_completed_browser_observation_is_accepted(self):
        validate_browser_report(self.report, self.bundle)

    def test_development_wrong_source_images_identity_or_failed_browser_are_rejected(self):
        for change in ({'scope': 'development-https-browser'}, {'identity': {}},
                       {'source_sha256': 'c' * 64}, {'images': {}}, {'result': 'fail'},
                       {'browser': {'result': 'fail'}}, {'runner_inputs': {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_browser_report({**self.report, **change}, self.bundle)

    def test_stale_future_reversed_or_out_of_fixture_browser_times_are_rejected(self):
        changes = ({'started_at_utc': '2026-10-20T00:00:10Z'},
                   {'completed_at_utc': '2026-10-25T00:00:50Z'},
                   {'completed_at_utc': '2026-10-23T00:00:50Z'},
                   {'started_at_utc': '2026-10-23T23:59:59Z'},
                   {'completed_at_utc': '2026-10-24T00:01:01Z'})
        for change in changes:
            report = copy.deepcopy(self.report)
            report['browser'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_browser_report(report, self.bundle)
