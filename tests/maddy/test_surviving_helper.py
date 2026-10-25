import os
import signal
import sqlite3
import time
import unittest
import uuid
from unittest.mock import patch

from helper.process import _collect
from helper.protocol import HelperRequest
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture
from tests.process_fault_support import kill_at_observed_boundary


def helper_with_observed_cli(channel, operation):
    fixture, request = operation
    update, blocked = fixture.helper.journal.update, None

    def journaled(identifier, values):
        nonlocal blocked
        record = update(identifier, values)
        if values.get('phase') == 'credentials-created':
            blocked = sqlite3.connect(fixture.data / 'credentials.db')
            blocked.execute('BEGIN IMMEDIATE')
        return record

    def collect(process, timeout):
        if tuple(process.args[-3:]) != ('creds', 'create', request.email):
            return _collect(process, timeout)
        wait_for_native_cli(fixture, request.email)
        fixture.command(('pause', fixture.identifier))
        channel.send(('helper_cli_paused', None))
        signal.pause()
        raise RuntimeError('The selected helper termination did not occur')

    try:
        with (patch.object(fixture.helper.journal, 'update', side_effect=journaled),
              patch('helper.process._collect', side_effect=collect)):
            fixture.helper.handle(request)
    finally:
        if blocked is not None:
            blocked.close()


def wait_for_native_cli(fixture, email):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        output = fixture.command(('top', fixture.identifier, '-eo', 'pid,args')).stdout
        if 'creds create ' + email in output:
            return
        time.sleep(0.05)
    raise RuntimeError('The real Maddy credentials writer never became observable')


class SurvivingHelperTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        root = self.fixture.root
        state, renewal = root / 'generations.json', root / 'renewal.lock'
        atomic_json(state, {'consumers': {}})
        renewal.touch(mode=0o600)
        generations = Generations(GenerationPaths(root, state), (
            CertificatePolicy(os.getgid(), os.getuid()), PairValidator(), time.time,
        ))
        self.coordination = Coordination(MaintenanceLocks(
            renewal, self.fixture.helper.dependencies.lock, LockPolicy(1, os.getuid()),
        ), (self.fixture.helper.journal, generations))

    def assert_backup_excluded(self):
        with self.assertRaisesRegex(ServiceError, 'helper_reconciliation_required'):
            with self.coordination.consistent():
                self.fail('A released helper lock authorized conflicting backup capture')

    def test_sigkill_parent_keeps_actual_cli_survivor_and_blocks_backup_and_duplicate_writes(self):
        request = HelperRequest('create', str(uuid.uuid4()), 'surviving-helper@rsalehin24.me',
                                MAILBOX_PASSWORD)
        try:
            kill_at_observed_boundary(helper_with_observed_cli, (self.fixture, request),
                                      ('helper_cli_paused', None))
            wait_for_native_cli(self.fixture, request.email)
            self.assertEqual(self.fixture.helper.journal.lookup(request.request_id)['phase'],
                             'credentials-created')
            self.assert_backup_excluded()
            with patch.object(self.fixture.maddy, 'change',
                              wraps=self.fixture.maddy.change) as work:
                outcome = self.fixture.helper.handle(request)
                self.assertNotEqual(outcome['state'], 'complete')
                with self.assertRaisesRegex(ServiceError, 'reconciliation_required'):
                    self.fixture.helper.handle(HelperRequest(
                        'create', str(uuid.uuid4()), 'conflicting-helper@rsalehin24.me',
                        MAILBOX_PASSWORD,
                    ))
            work.assert_not_called()
        finally:
            state = self.fixture.command(('inspect', '--format', '{{.State.Paused}}',
                                           self.fixture.identifier)).stdout.strip()
            if state == 'true':
                self.fixture.command(('unpause', self.fixture.identifier))
        self.wait_for_native_completion(request.email)
        self.assert_backup_excluded()

    def wait_for_native_completion(self, email):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            inventory = self.fixture.maddy.inventories()
            if email in inventory['credentials']:
                self.assertEqual(inventory['storage'], [])
                return
            time.sleep(0.1)
        self.fail('The surviving actual Maddy CLI never completed after its parent was killed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
