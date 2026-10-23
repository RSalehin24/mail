import unittest
import uuid
from unittest.mock import patch

from helper.protocol import HelperRequest
from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_crash_support import (
    CommandBarrier,
    DeletionEnabledRuntime,
    kill_pending_api,
    wait_for_complete,
)


class ApiCrashTests(unittest.TestCase):
    def setUp(self):
        self.mail = RealMaddyFixture()
        self.addCleanup(self.mail.close)
        self.mail.start()
        self.mail.deletion.enabled = True
        self.web = DeletionEnabledRuntime(self.mail.helper)
        self.addCleanup(self.web.close)
        self.web.start()
        authentication = RuntimeAuthentication(self, self.web)
        authentication.enroll()
        authentication.login()
        authentication.reauthenticate()

    def existing_mailbox(self, email):
        result = self.mail.helper.handle(HelperRequest('create', str(uuid.uuid4()),
                                                       email, MAILBOX_PASSWORD))
        self.assertEqual(result['state'], 'complete')
        deliver(self.mail, email, 'api-crash-message-must-survive')
        self.assertEqual(self.web.api('GET', '/api/accounts')[0], 200)

    def crash_and_retry(self, request, command):
        barrier = CommandBarrier(self.mail.maddy.change, command)
        identifier = request[2]['request_id']
        with patch.object(self.mail.maddy, 'change', side_effect=barrier):
            kill_pending_api(self, self.web, (barrier, request))
            record = wait_for_complete(self.mail.helper.journal, identifier)
            self.assertEqual(record['state'], 'complete')
            self.web.command(('start', self.web.identifier))
            self.web.ready()
            before = len(barrier.arguments)
            status, headers, result = self.web.api(*request)
            self.assertEqual((status, result['state']), (200, 'complete'))
            self.assertEqual(headers['cache-control'], 'no-store')
            self.assertEqual(len(barrier.arguments), before)
        mutations = [arguments for arguments in barrier.arguments if arguments[:2] == command]
        self.assertEqual(len(mutations), 1)
        self.assertEqual(self.mail.helper.journal.lookup(identifier), record)

    def assert_mail(self, email, password):
        with imap_login(self.mail, email, password) as connection:
            self.assertIn(b'api-crash-message-must-survive', bodies(connection))

    def test_sigkill_https_during_create_retains_helper_completion_and_retry_commits_metadata(self):
        email = 'api-crash-create@rsalehin24.me'
        payload = {'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD,
                   'display_name': 'Crash fixture', 'notes': 'Recovered only by the original ID'}
        self.crash_and_retry(('POST', '/api/accounts/create', payload), ('creds', 'create'))
        deliver(self.mail, email, 'api-crash-message-must-survive')
        self.assert_mail(email, MAILBOX_PASSWORD)
        status, _, accounts = self.web.api('GET', '/api/accounts')
        self.assertEqual(status, 200)
        self.assertEqual((accounts[0]['display_name'], accounts[0]['notes']),
                         (payload['display_name'], payload['notes']))

    def test_sigkill_https_during_reset_never_repeats_password_change_or_removes_mail(self):
        email = 'api-crash-reset@rsalehin24.me'
        self.existing_mailbox(email)
        password = '  changed after API process death  '
        request = ('POST', '/api/accounts/' + email + '/password',
                   {'request_id': str(uuid.uuid4()), 'password': password})
        self.crash_and_retry(request, ('creds', 'password'))
        self.assert_mail(email, password)
        self.assertEqual(self.web.api('GET', '/api/accounts')[0], 200)

    def test_sigkill_https_during_authorized_fixture_delete_never_repeats_removal(self):
        email = 'api-crash-delete@rsalehin24.me'
        self.existing_mailbox(email)
        request = ('DELETE', '/api/accounts/' + email,
                   {'request_id': str(uuid.uuid4()), 'confirmation': email,
                    'acknowledged': True, 'cutoff': 'synthetic separately authorized fixture'})
        self.crash_and_retry(request, ('creds', 'remove'))
        self.assertEqual(self.mail.maddy.inventories(), {'credentials': [], 'storage': []})
        with self.web.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
