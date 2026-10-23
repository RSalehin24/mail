import unittest
from unittest.mock import MagicMock

from host.backup import FULL_STATE_SOURCES
from host.baseline import BaselineServices
from shared.errors import ServiceError


class BaselinePolicyTests(unittest.TestCase):
    def services(self, present, sources):
        docker = MagicMock()
        docker.run.return_value = "mail-admin" if present else ""
        deployment = {
            "inputs": {"operation_timeout_seconds": 1},
            "baseline_maddy_mounts": {},
        }
        services = BaselineServices(docker, deployment, sources)
        services.maddy = MagicMock()
        services.maddy.inventories.return_value = {"credentials": [], "storage": []}
        return services

    def test_initial_install_requires_no_nonexistent_dashboard_state(self):
        services = self.services(False, {"maddy", "configuration", "tls"})
        self.assertEqual(services.inventories(), {"credentials": [], "storage": []})
        services.maddy.validate.assert_called_once()

    def test_an_existing_dashboard_requires_every_full_state_source_before_capture_or_pause(self):
        services = self.services(True, set(FULL_STATE_SOURCES))
        services.inventories()
        for omitted in FULL_STATE_SOURCES:
            with self.subTest(omitted=omitted):
                self._missing_source(omitted)

    def _missing_source(self, omitted):
        services = self.services(True, set(FULL_STATE_SOURCES) - {omitted})
        with self.assertRaisesRegex(ServiceError, "baseline_dashboard_sources_incomplete"):
            services.inventories()
        services.maddy.validate.assert_not_called()
        services.docker.run.assert_called_once_with(
            ("ps", "--all", "--filter", "name=^/mail-admin$", "--format", "{{.Names}}")
        )

    def test_stopped_dashboard_is_observed_as_existing(self):
        services = self.services(False, {"maddy"})
        services.docker.run.side_effect = lambda argv: "mail-admin" if "--all" in argv else ""
        with self.assertRaisesRegex(ServiceError, "baseline_dashboard_sources_incomplete"):
            services.inventories()

    def test_unexpected_container_observation_is_not_treated_as_a_fresh_install(self):
        services = self.services(False, {"maddy"})
        services.docker.run.return_value = "unexpected-container"
        with self.assertRaisesRegex(ServiceError, "baseline_dashboard_identity_unknown"):
            services.inventories()
