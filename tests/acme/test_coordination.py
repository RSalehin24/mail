import dataclasses
import signal
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from helper.protocol import HelperRequest
from shared.errors import ServiceError
from tests.certbot_case_support import PrivateAcmeCase
from tests.helper_support import MAILBOX_PASSWORD
from tests.process_fault_support import kill_at_observed_boundary
from tests.renewal_support import PrivateRenewal


def paused_renewal_process(channel, host):
    original = host.docker.run

    def run(arguments):
        if arguments[:2] != ('start', '--attach'):
            return original(arguments)
        identifier = arguments[-1]
        host.docker.docker.run(('start', identifier))
        host.docker.docker.run(('pause', identifier))
        channel.send(('certbot_paused', None))
        signal.pause()
        raise RuntimeError('The selected termination was not delivered')

    with patch.object(host.docker, 'run', side_effect=run):
        host.renewal.run(host.docker.client.pair)
    channel.send(('unexpected_completion', None))


class AcmeCoordinationTests(PrivateAcmeCase):
    def setUp(self):
        super().setUp()
        self.fixture.start()
        self.host = PrivateRenewal(self.certbot)
        self.host.coordination.locks = dataclasses.replace(
            self.host.coordination.locks, maddy=self.fixture.helper.dependencies.lock,
        )
        self.host.coordination.journal = self.fixture.helper.journal
        self.certbot.make_renewal_due()

    def create_mailbox(self, local):
        request = HelperRequest('create', str(uuid.uuid4()), local + '@rsalehin24.me',
                                MAILBOX_PASSWORD)
        self.assertEqual(self.fixture.helper.handle(request)['state'], 'complete')
        self.assertIn(request.email, self.fixture.maddy.inventories()['storage'])

    def pause_started_client(self, identifier):
        self.host.docker.docker.run(('start', identifier))
        self.fixture.command(('pause', identifier))
        state = self.host.docker.inspect(identifier)['State']
        self.assertTrue(state['Running'])
        self.assertTrue(state['Paused'])

    def complete_started_client(self, identifier):
        self.fixture.command(('unpause', identifier))
        self.assertEqual(self.host.docker.docker.run(('wait', identifier)), '0')

    def assert_backup_excluded(self, code):
        with self.assertRaisesRegex(ServiceError, code):
            with self.host.coordination.consistent():
                self.fail('Backup overlapped an unfinished real Certbot run')

    def test_real_acquisition_allows_mailbox_cli_but_excludes_capture_until_completion(self):
        entered, release = threading.Event(), threading.Event()
        original = self.host.docker.run

        def run(arguments):
            if arguments[:2] != ('start', '--attach'):
                return original(arguments)
            identifier = arguments[-1]
            self.pause_started_client(identifier)
            entered.set()
            try:
                if not release.wait(20):
                    raise RuntimeError('The coordination observer did not release Certbot')
            finally:
                self.complete_started_client(identifier)
            return ''

        with (patch.object(self.host.docker, 'run', side_effect=run),
              ThreadPoolExecutor(max_workers=1) as workers):
            renewal = workers.submit(self.host.renewal.run, self.certbot.pair)
            try:
                self.assertTrue(entered.wait(15), 'The actual Certbot writer never started')
                self.create_mailbox('acme-concurrent')
                self.assert_backup_excluded('maintenance_busy')
            finally:
                release.set()
            renewal.result(timeout=90)
        with self.host.coordination.consistent():
            self.assertEqual(self.host.generations.read()['acme_run']['exit_code'], 0)
        self.assertEqual(len(self.host.docker.created), 1)

    def test_lost_attachment_releases_parent_locks_but_live_certbot_still_blocks_capture(self):
        original = self.host.docker.run

        def disconnected(arguments):
            if arguments[:2] != ('start', '--attach'):
                return original(arguments)
            self.pause_started_client(arguments[-1])
            raise ServiceError('observed_lost_fixture_attachment', 503)

        with patch.object(self.host.docker, 'run', side_effect=disconnected):
            with self.assertRaisesRegex(ServiceError, 'acme_completion_unknown'):
                self.host.renewal.run(self.certbot.pair)
        identifier = self.host.generations.read()['acme_run']['container_id']
        try:
            self.assert_backup_excluded('acme_completion_unknown')
            self.create_mailbox('acme-surviving')
            with self.assertRaisesRegex(ServiceError, 'acme_completion_unknown'):
                self.host.certbot.reconcile()
        finally:
            self.complete_started_client(identifier)
        self.host.certbot.reconcile()
        with self.host.coordination.consistent():
            self.assertEqual(self.host.generations.read()['acme_run']['exit_code'], 0)
        self.assertEqual(self.host.docker.created, {identifier})

    def test_sigkill_of_host_renewal_preserves_surviving_certbot_exclusion_until_reconciled(self):
        kill_at_observed_boundary(paused_renewal_process, self.host, ('certbot_paused', None))
        run = self.host.generations.read()['acme_run']
        self.assertEqual(run['state'], 'starting')
        self.host.docker.adopt_recorded_run(run)
        identifier = run['container_id']
        try:
            state = self.host.docker.inspect(identifier)['State']
            self.assertTrue(state['Running'] and state['Paused'])
            self.assert_backup_excluded('acme_completion_unknown')
            self.create_mailbox('acme-killed-parent')
            with self.assertRaisesRegex(ServiceError, 'acme_completion_unknown'):
                self.host.certbot.reconcile()
        finally:
            self.complete_started_client(identifier)
        self.host.certbot.reconcile()
        self.host.certbot.reconcile()
        with self.host.coordination.consistent():
            self.assertEqual(self.host.generations.read()['acme_run']['exit_code'], 0)
        self.assertEqual(self.host.docker.created, {identifier})
