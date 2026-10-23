import unittest
from pathlib import Path

from tests.host_dependency_support import DependencyFixture
from tests.host_support import GuestHelper


class UnitDependencyRecoveryTests(unittest.TestCase):
    def test_recovered_docker_dependency_starts_the_helper_after_an_initial_failure(self):
        host = GuestHelper.load()
        source = Path('/etc/systemd/system/mail-admin-helper.service')
        fixture = DependencyFixture(host.record['instance_id'], source)
        observation = fixture.run()
        self.assertTrue((observation / 'observation.json').is_file())
        host.ready()
