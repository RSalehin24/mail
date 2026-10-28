"""Real HTTPS, Unix helper, Maddy data and worker; no production addresses are mutated."""

import dataclasses
import imaplib
import sqlite3
import time
import unittest
import uuid
from types import SimpleNamespace

from host.mailbox_recovery import MailboxRecovery
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.recovery_support import qualify_fixture
from tests.restore_support import FixtureWriters, bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class RecoveryHttpsTests(unittest.TestCase):
    def test_recoverable_delete_restore_and_retry_over_actual_https(self):
        mail = RealMaddyFixture()
        self.addCleanup(mail.close)
        mail.start()
        web = RuntimeFixture(mail.helper)
        self.addCleanup(web.close)
        writers = FixtureWriters(mail)
        root = mail.root / 'recovery'
        root.mkdir(mode=0o700)
        (root / 'recovery-worker.lock').touch(mode=0o600)
        context = SimpleNamespace(
            deployment={**mail.maddy.deployment, 'maddy_data_directory': str(mail.data),
                        'maddy_message_paths': ['messages'],
                        'backup_sources': {'dashboard': str(web.directories['data'])}},
            docker=SimpleNamespace(inspect=lambda name: {}),
            generations=SimpleNamespace(read=lambda: {'consumers': {'maddy': {}}}),
            coordination=writers,
        )
        worker = MailboxRecovery(context, root)
        worker.maddy, worker.writers = mail.maddy, writers
        policy = {'schema_version': 1, 'revision': 1, 'enabled': True,
                  'permanent_deletion_enabled': False, 'default_retention': '7_days',
                  'protected': {'other@rsalehin24.me': 'Operational mailbox'}}
        atomic_json(root / 'recovery-policy.json', policy)
        qualify_fixture(root)
        mail.helper.dependencies = dataclasses.replace(mail.helper.dependencies,
                                                       recovery=worker.control)
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
        status, _, original = web.api('GET', '/api/accounts/' + email)
        self.assertEqual(status, 200)
        generation = mail.helper.journal.incarnation(email)['generation']
        deliver(mail, email, 'https-recoverable-message')
        deliver(mail, 'other@rsalehin24.me', 'https-other-preserved')
        identifier = str(uuid.uuid4())
        payload = {'request_id': identifier, 'email': email, 'confirmation': email,
                   'acknowledged': True, 'retention': '7_days', 'policy_revision': 1}
        status, _, queued = web.api('POST', '/api/recovery/delete', payload)
        self.assertEqual(status, 200)
        self.assertEqual(queued['state'], 'queued')
        self.assertEqual(web.api('POST', '/api/accounts/create', {
            'request_id': str(uuid.uuid4()), 'email': 'blocked@rsalehin24.me',
            'password': MAILBOX_PASSWORD,
        })[0], 409)
        started = time.monotonic()
        worker.run()
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(web.api('GET', '/api/recovery/operations/' + identifier)[2]['state'],
                         'complete')
        self.assertEqual(web.api('POST', '/api/recovery/delete', payload)[0], 200)
        deleted = web.api('GET', '/api/recovery/mailboxes')[2]['mailboxes']
        self.assertEqual(deleted[0]['email'], email)
        self.assertTrue(deleted[0]['recovery_available'])
        self.assertEqual(deleted[0]['deadline'] - deleted[0]['deleted_at'], 7 * 86400)
        with sqlite3.connect(web.database.path) as connection:
            self.assertIsNone(connection.execute('SELECT 1 FROM mailboxes WHERE email=?',
                                                 (email,)).fetchone())
        restore_id = str(uuid.uuid4())
        restored_password = 'HTTPS restored private password 2026'
        restore = {'request_id': restore_id, 'confirmation': email, 'acknowledged': True,
                   'password': restored_password}
        endpoint = '/api/recovery/mailboxes/' + identifier + '/restore'
        self.assertEqual(web.api('POST', endpoint, restore)[0], 200)
        self.assertEqual(web.api('POST', endpoint, restore)[0], 200)
        worker.run()
        self.assertEqual(web.api('GET', '/api/recovery/operations/' + restore_id)[2]['state'],
                         'complete')
        result = web.api('GET', '/api/accounts/' + email)[2]
        self.assertEqual(result['created'], original['created'])
        self.assertNotEqual(mail.helper.journal.incarnation(email)['generation'], generation)
        with imap_login(mail, email, restored_password) as connection:
            self.assertIn(b'https-recoverable-message', bodies(connection))
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(mail, email, MAILBOX_PASSWORD)
        with imap_login(mail, 'other@rsalehin24.me', MAILBOX_PASSWORD) as connection:
            self.assertIn(b'https-other-preserved', bodies(connection))
        self.assertEqual(web.api('POST', '/api/recovery/mailboxes/' + identifier + '/remove', {
            'request_id': str(uuid.uuid4()), 'confirmation': email, 'acknowledged': True,
        })[0], 403)
        logs = web.command(('logs', web.identifier))
        self.assertNotIn(restored_password, logs.stdout + logs.stderr)
