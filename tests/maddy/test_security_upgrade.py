"""Exercise the pinned security release with real mail and an offline 0.9.5 database."""

import shutil

from helper.maddy import DockerMaddy
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy import test_real_maddy
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver

SECURITY_IMAGE = (
    'ghcr.io/foxcpp/maddy@sha256:'
    'e1074e92a452b84d1135ebe8ab831c5f541d33dcbd39e38fab740a53bbf2f97c'
)


class SecurityMaddy(DockerMaddy):
    def __init__(self, deployment, executable):
        super().__init__({**deployment, 'maddy_cli_version': '0.9.6'}, executable)


class SecurityFixture(RealMaddyFixture):
    maddy_class = SecurityMaddy

    def image_reference(self):
        return SECURITY_IMAGE


class SecurityUpgradeTests(test_real_maddy.RealMaddyTests):
    def setUp(self):
        self.fixture = SecurityFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def test_offline_previous_database_preserves_mail_and_the_original_rollback_copy(self):
        old = RealMaddyFixture()
        self.addCleanup(old.close)
        old.start()
        # Reuse the same helper operations and realistic baseline account set.
        current = self.fixture
        self.fixture = old
        try:
            self.test_01_eight_baseline_pairs_and_real_delivery()
            deliver(old, 'mail@rsalehin24.me', 'security-upgrade-cutoff-message')
        finally:
            self.fixture = current
        expected = old.maddy.inventories()
        old.command(('stop', old.identifier))
        current.command(('stop', current.identifier))
        shutil.rmtree(current.data)
        shutil.copytree(old.data, current.data)
        current.command(('start', current.identifier))
        current.refresh_ports()
        current._wait()
        self.assertEqual(current.maddy.inventories(), expected)
        with imap_login(current, 'mail@rsalehin24.me', MAILBOX_PASSWORD) as connection:
            self.assertIn(b'security-upgrade-cutoff-message', bodies(connection))
        current.command(('stop', current.identifier))
        old.command(('start', old.identifier))
        old.refresh_ports()
        old._wait()
        self.assertEqual(old.maddy.inventories(), expected)
        with imap_login(old, 'mail@rsalehin24.me', MAILBOX_PASSWORD) as connection:
            self.assertIn(b'security-upgrade-cutoff-message', bodies(connection))
