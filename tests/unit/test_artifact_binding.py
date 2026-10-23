import json
import unittest

from host.preflight import _configuration_binding
from shared.errors import ServiceError
from tests.support import StateFixture
from tools.prepare_release import pending_configuration


class ArtifactBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)

    def test_release_configuration_keeps_operator_inputs_pending_and_capabilities_off(self):
        record = pending_configuration({"Id": "synthetic-image"})
        self.assertFalse(record["inputs"]["email_otp_enabled"])
        self.assertFalse(record["inputs"]["permanent_deletion_enabled"])
        self.assertIsNone(record["inputs"]["backup_destination"])
        self.assertIsNone(record["inputs"]["maintenance_window"])
        self.assertEqual(record["inputs"]["mx_mode"], "cloudflare")

    def test_only_explicit_operator_choices_replace_pending_inputs(self):
        record = pending_configuration(
            {"Id": "synthetic-image"}, {"alert_recipient": "rsalehin24@gmail.com"}
        )
        self.assertEqual(record["inputs"]["alert_recipient"], "rsalehin24@gmail.com")
        self.assertEqual(record["alert_configuration"]["recipient"], "rsalehin24@gmail.com")
        self.assertIsNone(record["inputs"]["backup_destination"])
        self.assertIsNone(record["inputs"]["alert_channel"])
        with self.assertRaises(ValueError):
            pending_configuration({"Id": "synthetic-image"}, {"unknown": "value"})

    def test_effective_configuration_binding_is_type_sensitive_and_checks_more_than_inputs(self):
        qualified = {"inputs": {"service_uid": 10001}, "helper_protocol": 1}
        (self.fixture.root / "deployment.json").write_text(json.dumps(qualified))
        evidence = {"artifacts": [{"id": "deployment_config", "path": "deployment.json"}]}
        _configuration_binding(qualified, (self.fixture.root, evidence))
        for changed in (
            {**qualified, "helper_protocol": True},
            {**qualified, "unexpected": "value"},
        ):
            with self.assertRaises(ServiceError):
                _configuration_binding(changed, (self.fixture.root, evidence))
