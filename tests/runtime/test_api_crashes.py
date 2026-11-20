"""The dashboard dies right after a real Maddy command ran; a retry must never repeat it."""

import unittest
import uuid

from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy_support import UnprivilegedMaddyFixture, imap_login
from tests.restore_support import bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_crash_support import DeletionEnabledRuntime, kill_pending_api


class ApiCrashTests(unittest.TestCase):
    def setUp(self):
        self.mail = UnprivilegedMaddyFixture()
        self.addCleanup(self.mail.close)
        self.mail.start()
        self.web = DeletionEnabledRuntime(self.mail)
        self.addCleanup(self.web.close)
        self.web.start()
        authentication = RuntimeAuthentication(self, self.web)
        authentication.enroll()
        authentication.login()
        authentication.reauthenticate()

    def existing_mailbox(self, email):
        status, _, result = self.web.api('POST', '/api/accounts/create', {
            'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD})
        self.assertEqual((status, result['state']), (200, 'complete'))
        deliver(self.mail, email, 'api-crash-message-must-survive')

    def crash_and_retry(self, request, subcommand):
        """Kill the dashboard after the command ran; the retry must not run it again."""
        identifier = request[2]['request_id']
        before = len(self.web.calls(subcommand))
        self.web.hold_after(subcommand)
        kill_pending_api(self, self.web, request)
        self.assertEqual(self.web.journal().lookup(identifier)['state'], 'pending')
        self.web.command(('start', self.web.identifier))
        self.web.ready()
        status, headers, result = self.web.api(*request)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(len(self.web.calls(subcommand)) - before, 1)
        # An interrupted step is never replayed or guessed complete: it stays unresolved.
        self.assertNotEqual(result.get('state'), 'complete')
        record = self.web.journal().lookup(identifier)
        self.assertEqual((record['state'], record['phase'] is not None), ('pending', True))
        self.assertEqual(self.web.api(*request)[2], result)
        self.assertEqual(len(self.web.calls(subcommand)) - before, 1)
        return status, result

    def assert_mail(self, email, password):
        with imap_login(self.mail, email, password) as connection:
            self.assertIn(b'api-crash-message-must-survive', bodies(connection))

    def test_sigkill_during_create_never_repeats_creation_and_the_operation_stays_unresolved(self):
        email = 'api-crash-create@rsalehin24.me'
        payload = {'request_id': str(uuid.uuid4()), 'email': email, 'password': MAILBOX_PASSWORD,
                   'display_name': 'Crash fixture', 'notes': 'Recovered only by the original ID'}
        self.crash_and_retry(('POST', '/api/accounts/create', payload), 'creds create')
        self.assertEqual(self.web.calls('imap-acct create'), [])
        self.assertEqual(self.web.api('GET', '/api/accounts')[0], 409)
        self.assertEqual(self.mail.maddy.inventories()['credentials'], [email])

    def test_sigkill_during_reset_never_repeats_password_change_or_removes_mail(self):
        email = 'api-crash-reset@rsalehin24.me'
        self.existing_mailbox(email)
        password = '  changed after API process death  '
        request = ('POST', '/api/accounts/' + email + '/password',
                   {'request_id': str(uuid.uuid4()), 'password': password})
        self.crash_and_retry(request, 'creds password')
        self.assert_mail(email, password)
        self.assertEqual(self.web.api('GET', '/api/accounts')[0], 409)

    def test_sigkill_during_authorized_fixture_delete_never_repeats_removal(self):
        email = 'api-crash-delete@rsalehin24.me'
        self.existing_mailbox(email)
        request = ('DELETE', '/api/accounts/' + email,
                   {'request_id': str(uuid.uuid4()), 'confirmation': email,
                    'acknowledged': True, 'cutoff': self.web.authorize(email)})
        self.crash_and_retry(request, 'creds remove')
        self.assertEqual(self.web.calls('imap-acct remove'), [])
        self.assertEqual(self.mail.maddy.inventories()['credentials'], [])
