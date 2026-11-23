"""Real HTTPS, in-process host and Maddy data: direct delete, then the owner's restore command."""

import imaplib
import os
import sqlite3
import time
import unittest
import uuid

from host.restore_mailbox import MailboxRestore
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import SERVICE_ID, UnprivilegedMaddyFixture, imap_login
from tests.restore_support import bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class RecoveryHttpsTests(unittest.TestCase):
    def test_direct_delete_then_owner_restore_over_actual_https(self):
        mail = UnprivilegedMaddyFixture()
        self.addCleanup(mail.close)
        mail.start()
        web = RuntimeFixture(mail)
        self.addCleanup(web.close)
        root = web.state
        policy = {'schema_version': 1, 'revision': 1, 'enabled': True,
                  'permanent_deletion_enabled': False, 'default_retention': '7_days',
                  'protected': {'other@rsalehin24.me': 'Operational mailbox'}}
        atomic_json(root / 'recovery-policy.json', policy)
        os.chown(root / 'recovery-policy.json', SERVICE_ID, SERVICE_ID)
        web.start()
        authentication = RuntimeAuthentication(self, web)
        authentication.enroll()
        authentication.login()
        authentication.reauthenticate()
        email = 'recover-https@rsalehin24.me'
        for account in (email, 'other@rsalehin24.me'):
            status, _, _ = web.api('POST', '/api/accounts/create', {
                'request_id': str(uuid.uuid4()), 'email': account, 'password': MAILBOX_PASSWORD,
            })
            self.assertEqual(status, 200)
        original = web.api('GET', '/api/accounts/' + email)[2]
        generation = web.journal().incarnation(email)['generation']
        deliver(mail, email, 'https-recoverable-message')
        deliver(mail, 'other@rsalehin24.me', 'https-other-preserved')
        payload = {'request_id': str(uuid.uuid4()), 'email': email, 'confirmation': email,
                   'acknowledged': True, 'retention': '7_days', 'policy_revision': 1}
        started = time.monotonic()
        status, _, result = web.api('POST', '/api/recovery/delete', payload)
        self.assertEqual((status, result['state']), (200, 'complete'))
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(web.api('POST', '/api/recovery/delete', payload)[0], 200)
        self.assertEqual(web.api('POST', '/api/accounts/create', {
            'request_id': str(uuid.uuid4()), 'email': 'unblocked@rsalehin24.me',
            'password': MAILBOX_PASSWORD,
        })[0], 200)
        deleted = web.api('GET', '/api/recovery/mailboxes')[2]['mailboxes']
        self.assertEqual(deleted[0]['email'], email)
        self.assertTrue(deleted[0]['recovery_available'])
        self.assertEqual(deleted[0]['deadline'] - deleted[0]['deleted_at'], 7 * 86400)
        with sqlite3.connect(web.database.path) as connection:
            self.assertIsNone(connection.execute('SELECT 1 FROM mailboxes WHERE email=?',
                                                 (email,)).fetchone())
        restored_password = 'HTTPS restored private password 2026'
        deployment = {'maddy_data_directory': str(mail.data), 'state_owner_uid': SERVICE_ID,
                      'backup_sources': {'dashboard': str(web.directories['data'])}}
        MailboxRestore(deployment, root, mail.maddy).restore(
            deleted[0]['id'], restored_password)
        result = web.api('GET', '/api/accounts/' + email)[2]
        self.assertEqual(result['created'], original['created'])
        self.assertNotEqual(web.journal().incarnation(email)['generation'], generation)
        with imap_login(mail, email, restored_password) as connection:
            self.assertIn(b'https-recoverable-message', bodies(connection))
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(mail, email, MAILBOX_PASSWORD)
        with imap_login(mail, 'other@rsalehin24.me', MAILBOX_PASSWORD) as connection:
            self.assertIn(b'https-other-preserved', bodies(connection))
        self.assertEqual(web.api('POST', '/api/recovery/mailboxes/' + deleted[0]['id']
                                 + '/restore', {})[0], 404)
        logs = web.command(('logs', web.identifier))
        self.assertNotIn(restored_password, logs.stdout + logs.stderr)
