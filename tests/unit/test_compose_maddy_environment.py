import re
import unittest
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
NAMES = ("MADDY_DOMAIN", "MADDY_HOSTNAME")


def service_settings(service: str) -> dict[str, str]:
    text = COMPOSE.read_text()
    body = re.search(rf"^  {service}:\n(.*?)(?=^  \S|\Z)", text, re.M | re.S).group(1)
    return {name: re.search(rf"^      {name}: (\S+)$", body, re.M).group(1) for name in NAMES}


class ComposeMaddyEnvironmentTests(unittest.TestCase):
    def test_dashboard_gets_the_variables_maddy_conf_needs_to_parse(self):
        # The dashboard runs the Maddy CLI on the same config, which reads both with {env:...}.
        self.assertEqual(service_settings("mail-admin"), service_settings("maddy"))
