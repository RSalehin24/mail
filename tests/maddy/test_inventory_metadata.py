import dataclasses
import imaplib
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.database import Database
from backend.mailboxes import Mailboxes
from helper.protocol import HelperRequest
from helper.service import HostHelper
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from tests.api_support import DirectHelper
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class InventoryAndMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        self.database = Database.provision(self.fixture.root / 'metadata.db', str(uuid.uuid4()))
        self.mailboxes = Mailboxes((self.database, DirectHelper(self.fixture.helper), time.time))

    def request(self, local):
        return {'request_id': str(uuid.uuid4()), 'email': local + '@rsalehin24.me',
                'password': MAILBOX_PASSWORD, 'display_name': 'Original fixture name',
                'notes': 'Original fixture purpose'}

    def assert_body(self, email, expected):
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            self.assertIn(expected, bodies(connection))

    def assert_only_observations(self, calls):
        commands = {('creds', 'list'), ('imap-acct', 'list'), ('version',)}
        self.assertTrue(all(call.args[0] in commands for call in calls))

    def test_eight_real_pairs_and_both_partial_inventories_preserve_metadata(self):
        requests = [self.request(local) for local in (
            'cloud', 'demo_user.library', 'dev_01', 'dev_02', 'library', 'mail', 'reyan', 'salehin',
        )]
        for request in requests:
            self.assertEqual(self.mailboxes.create(request)['state'], 'complete')
        original = self.mailboxes.inventory()
        self.assertEqual(len(original), 8)
        self.assertTrue(all(row['credentials_present'] and row['storage_present']
                            for row in original))
        self.fixture.maddy.change(('creds', 'remove', '--yes', requests[0]['email']))
        self.fixture.maddy.change(('imap-acct', 'remove', '--yes', requests[1]['email']))
        rows = {row['email']: row for row in self.mailboxes.inventory()}
        self.assertEqual(len(rows), 8)
        for index, presence in ((0, (False, True)), (1, (True, False))):
            row = rows[requests[index]['email']]
            self.assertEqual((row['credentials_present'], row['storage_present']), presence)
            self.assertEqual(row['status'], 'reconciliation_required')
        for row in original:
            self.assertEqual(rows[row['email']]['display_name'], row['display_name'])
            self.assertEqual(rows[row['email']]['notes'], row['notes'])
            self.assertEqual(rows[row['email']]['created'], row['created'])

    def test_create_conflicts_with_either_store_without_granting_orphaned_mail_access(self):
        for store in ('credentials', 'storage'):
            self.assert_partial_create_conflict(store)

    def assert_partial_create_conflict(self, store):
        request = self.request('partial-' + store)
        self.mailboxes.create(request)
        deliver(self.fixture, request['email'], 'orphaned-original-message')
        command = 'imap-acct' if store == 'credentials' else 'creds'
        self.fixture.maddy.change((command, 'remove', '--yes', request['email']))
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            with self.assertRaises(ServiceError) as caught:
                self.mailboxes.create({**request, 'request_id': str(uuid.uuid4())})
        self.assertEqual(caught.exception.code, 'account_conflict')
        self.assert_only_observations(change.call_args_list)
        if store == 'storage':
            with self.assertRaises(imaplib.IMAP4.error):
                imap_login(self.fixture, request['email'], MAILBOX_PASSWORD)

    def test_duplicate_and_fresh_create_preserve_actual_password_messages_and_metadata(self):
        request = self.request('duplicate')
        self.mailboxes.create(request)
        deliver(self.fixture, request['email'], 'duplicate-mail-must-survive')
        changed = {**request, 'password': ' ignored different fixture password ',
                   'display_name': 'Changed fixture name', 'notes': 'Changed fixture notes'}
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            self.assertEqual(self.mailboxes.create(changed)['state'], 'complete')
            with self.assertRaises(ServiceError) as caught:
                self.mailboxes.create({**changed, 'request_id': str(uuid.uuid4())})
        self.assertEqual(caught.exception.code, 'account_conflict')
        self.assert_only_observations(change.call_args_list)
        row = self.mailboxes.inventory()[0]
        self.assertEqual((row['display_name'], row['notes']),
                         (request['display_name'], request['notes']))
        self.assert_body(request['email'], b'duplicate-mail-must-survive')
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(self.fixture, request['email'], changed['password'])

    def test_real_metadata_commit_failure_preserves_mail_and_retry_uses_completed_journal(self):
        request = self.request('metadata-failure')
        with self.database.transaction() as connection:
            connection.execute("CREATE TRIGGER reject_fixture_audit BEFORE INSERT ON audit_logs "
                               "BEGIN SELECT RAISE(ABORT,'fixture metadata commit failure'); END")
        with self.assertRaises(ServiceError) as caught:
            self.mailboxes.create(request)
        self.assertEqual(caught.exception.code, 'metadata_reconciliation_required')
        outcome = self.fixture.helper.journal.lookup(request['request_id'])
        self.assertEqual(outcome['state'], 'complete')
        deliver(self.fixture, request['email'], 'mail-after-metadata-failure')
        with self.database.transaction() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
            connection.execute('DROP TRIGGER reject_fixture_audit')
        self.assertEqual(self.mailboxes.inventory()[0]['display_name'], '')
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            self.assertEqual(self.mailboxes.create(request)['state'], 'complete')
        change.assert_not_called()
        self.assertEqual(self.mailboxes.inventory()[0]['display_name'], request['display_name'])
        self.assert_body(request['email'], b'mail-after-metadata-failure')

    def test_stale_reset_waits_for_deletion_lock_and_cannot_reinsert_missing_credentials(self):
        request = self.request('stale-reset')
        self.mailboxes.create(request)
        dependencies = dataclasses.replace(self.fixture.helper.dependencies,
                                           lock_policy=LockPolicy(5))
        helper = HostHelper(dependencies)
        stale = HelperRequest('password', str(uuid.uuid4()), request['email'], MAILBOX_PASSWORD)
        waiting = threading.Event()
        acquire = HostLock._acquire

        def observed(lock):
            waiting.set()
            return acquire(lock)

        with ThreadPoolExecutor(max_workers=1) as executor:
            with HostLock(dependencies.lock, dependencies.lock_policy):
                self.fixture.maddy.change(('creds', 'remove', '--yes', request['email']))
                with patch('host.locks.HostLock._acquire', observed):
                    future = executor.submit(helper.handle, stale)
                    self.assertTrue(waiting.wait(2))
                    self.assertFalse(future.done())
                    self.fixture.maddy.change(('imap-acct', 'remove', '--yes', request['email']))
            with self.assertRaises(ServiceError) as caught:
                future.result(timeout=10)
        self.assertEqual(caught.exception.code, 'account_not_found')
        self.assertNotIn(request['email'], self.fixture.maddy.inventories()['credentials'])
        self.assertIsNone(helper.journal.lookup(stale.request_id))

    def test_reset_rejects_each_partial_inventory_before_any_password_upsert(self):
        for store in ('creds', 'imap-acct'):
            request = self.request('reset-partial-' + store)
            self.mailboxes.create(request)
            self.fixture.maddy.change((store, 'remove', '--yes', request['email']))
            self.assert_partial_reset_denied(request)

    def assert_partial_reset_denied(self, request):
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            with self.assertRaises(ServiceError) as caught:
                self.mailboxes.reset(request['email'], {**request, 'request_id': str(uuid.uuid4())})
        self.assertEqual(caught.exception.code, 'account_conflict')
        self.assert_only_observations(change.call_args_list)
