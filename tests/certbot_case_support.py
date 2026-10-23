"""Shared private CA and real initial issuance for actual certificate integration tests."""

import unittest

from tests.acme_network_support import AcmeNetwork
from tests.certbot_support import LocalCertbot
from tests.maddy_support import RealMaddyFixture


class PrivateAcmeCase(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.network = AcmeNetwork(self.fixture)
        self.addCleanup(self.network.close)
        self.network.start()
        self.certbot = LocalCertbot(self.fixture, self.network)
        issued = self.certbot.issue()
        self.assertEqual(issued.returncode, 0, (issued.stdout + issued.stderr)[-5000:])
