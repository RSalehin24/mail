import json
import unittest
import uuid

from helper.protocol import HelperRequest
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver
from tools.resources import MADDY_RUNTIME_TMPFS_BYTES


class RuntimePipeRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def test_unclean_maddy_exit_drops_runtime_sockets_and_keeps_mail_and_cli_usable(self):
        actual = json.loads(self.fixture.command(('inspect', self.fixture.identifier)).stdout)[0]
        self.assertEqual(actual['HostConfig']['Tmpfs']['/run/maddy'],
                         f'size={MADDY_RUNTIME_TMPFS_BYTES},mode=0700')
        email = 'unclean-restart@rsalehin24.me'
        outcome = self.fixture.helper.handle(HelperRequest(
            'create', str(uuid.uuid4()), email, MAILBOX_PASSWORD,
        ))
        self.assertEqual(outcome['state'], 'complete')
        deliver(self.fixture, email, 'unclean-restart-message-must-survive')
        self.fixture.command(('kill', '--signal', 'KILL', self.fixture.identifier))
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        self.assertEqual(self.fixture.maddy.inventories(),
                         {'credentials': [email], 'storage': [email]})
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            self.assertIn(b'unclean-restart-message-must-survive', bodies(connection))
