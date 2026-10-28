import json
import os
import unittest
from unittest.mock import patch

from host.independent_monitor import observe, verify
from shared.files import protected_json
from tests.support import StateFixture


class IndependentMonitorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        project = "projects/test-project"
        self.configuration = {
            "project": project,
            "checks": [project + "/uptimeCheckConfigs/https", project + "/uptimeCheckConfigs/imap"],
            "policies": [project + "/alertPolicies/policy"],
            "channels": [project + "/notificationChannels/email"],
            "credential_reference": str(self.fixture.root / "key.json"),
        }
        self.resources = {
            self.configuration["checks"][0]: {
                "name": self.configuration["checks"][0],
                "monitoredResource": {"labels": {"host": "mail.rsalehin24.me"}},
                "httpCheck": {"port": 443, "useSsl": True, "validateSsl": True},
            },
            self.configuration["checks"][1]: {
                "name": self.configuration["checks"][1],
                "monitoredResource": {"labels": {"host": "mail.rsalehin24.me"}},
                "tcpCheck": {"port": 993},
            },
            self.configuration["policies"][0]: {
                "enabled": True,
                "conditions": [{"filter": "https imap"}],
                "notificationChannels": self.configuration["channels"],
            },
            self.configuration["channels"][0]: {"enabled": True},
        }

    def test_disabled_policies_and_notification_channels_do_not_report_enabled(self):
        with patch(
            "host.independent_monitor.get_resource",
            side_effect=lambda name, *_: self.resources[name],
        ):
            self.assertTrue(verify(self.configuration, "token"))
            self.resources[self.configuration["policies"][0]]["enabled"] = False
            self.assertFalse(verify(self.configuration, "token"))
            self.resources[self.configuration["policies"][0]]["enabled"] = True
            self.resources[self.configuration["channels"][0]]["enabled"] = False
            self.assertFalse(verify(self.configuration, "token"))

    def test_failed_verification_does_not_refresh_last_success(self):
        path = self.fixture.root / "observation.json"
        path.write_text(json.dumps({"enabled": True, "last_verified_at": 1000}))
        path.chmod(0o600)
        with (
            patch(
                "host.independent_monitor.protected_json",
                side_effect=lambda p: protected_json(p, os.getuid()),
            ),
            patch("host.independent_monitor.access_token", side_effect=OSError("unavailable")),
        ):
            result = observe(self.configuration, path)
        self.assertIsNone(result["enabled"])
        self.assertEqual(result["outcome"], "verification_failed")
        self.assertEqual(result["last_verified_at"], 1000)
