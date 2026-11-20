"""Real HTTPS, in-process host, Maddy data and the root recovery worker; no production mutation."""

import imaplib
import os
import sqlite3
import time
import unittest
import uuid
from types import SimpleNamespace

from host.locks import LockPolicy
from host.mailbox_recovery import MailboxRecovery
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import SERVICE_ID, UnprivilegedMaddyFixture, imap_login
from tests.recovery_support import qualify_fixture
from tests.restore_support import FixtureWriters, bodies, deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class DashboardOwnedState:
    """The Maddy fixture, but coordinating through the dashboard's own journal and lock."""

    def __init__(self, mail, web):
        self.mail = mail
        self.helper = SimpleNamespace(
            journal=web.journal(),
            dependencies=SimpleNamespace(lock=web.lock, lock_policy=LockPolicy(5, SERVICE_ID)),
        )

    def __getattr__(self, name):
        return getattr(self.mail, name)


class RecoveryHttpsTests(unittest.TestCase):
    def test_recoverable_delete_restore_and_retry_over_actual_https(self):
        mail = UnprivilegedMaddyFixture()
        self.addCleanup(mail.close)
        mail.start()
        web = RuntimeFixture(mail)
        self.addCleanup(web.close)
        # The dashboard process owns the queue; the root worker shares its state directory.
        root = web.state
        policy = {'schema_version': 1, 'revision': 1, 'enabled': True,
                  'permanent_deletion_enabled': False, 'default_retention': '7_days',
                  'protected': {'other@rsalehin24.me': 'Operational mailbox'}}
        atomic_json(root / 'recovery-policy.json', policy)
        qualify_fixture(root)
        for name in ('recovery-policy.json', 'recovery-qualified.json'):
            # Root-owned records the dashboard may read through its group, as on the host.
            os.chown(root / name, 0, SERVICE_ID)
            (root / name).chmod(0o640)
        web.start()
        shared = DashboardOwnedState(mail, web)
        writers = FixtureWriters(shared)
        os.chown(writers.renewal, SERVICE_ID, SERVICE_ID)
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
        generation = web.journal().incarnation(email)['generation']
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
        # Known gap of the in-process host: availability is checked by the dashboard user, but
        # the recovery store must stay root-owned and 0700, so it cannot read it and reports
        # False. The restore below still works because the root worker verifies the archive.
        self.assertFalse(deleted[0]['recovery_available'])
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
        self.assertNotEqual(web.journal().incarnation(email)['generation'], generation)
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
