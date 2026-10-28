import imaplib
import sqlite3
import unittest
import uuid
from datetime import UTC, datetime

from helper.protocol import HelperRequest
from host.mailbox_store import extract_mailbox, import_mailbox
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class MailboxRecoveryStoreTests(unittest.TestCase):
    def test_isolated_extract_and_import_preserve_other_accounts_and_mail_metadata(self):
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        email, other = 'recover@rsalehin24.me', 'other@rsalehin24.me'
        for account in (email, other):
            fixture.helper.handle(HelperRequest('create', str(uuid.uuid4()),
                                               account, MAILBOX_PASSWORD))
        deliver(fixture, other, 'unrelated-user-remains')
        with imap_login(fixture, email, MAILBOX_PASSWORD) as connection:
            self.assertEqual(connection.create('Saved')[0], 'OK')
            self.assertEqual(connection.append('Saved', '(\\Seen \\Flagged custom)',
                datetime(2024, 8, 10, 14, 30, tzinfo=UTC),
                b'Subject: recovery fixture\r\n\r\nretained-body\r\n')[0], 'OK')
        fixture.command(('stop', fixture.identifier))
        database, objects = fixture.data / 'imapsql.db', fixture.data / 'messages'
        recovery = fixture.root / 'isolated-recovery'
        extract_mailbox(database, objects, email, recovery)
        with sqlite3.connect(recovery / 'imapsql.db') as connection:
            self.assertEqual(
                connection.execute('SELECT username FROM users').fetchall(), [(email,)],
            )
            before = connection.execute('SELECT date,seen,recent FROM msgs').fetchall()
            flags = connection.execute('SELECT flag FROM flags ORDER BY flag').fetchall()
        with self.assertRaisesRegex(ServiceError, 'account_conflict'):
            import_mailbox(database, objects, email, recovery)
        with sqlite3.connect(database) as connection:
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('DELETE FROM users WHERE username=?', (email,))
            connection.execute('DELETE FROM extKeys WHERE uid NOT IN (SELECT id FROM users)')
        with sqlite3.connect(fixture.data / 'credentials.db') as connection:
            connection.execute('DELETE FROM passwords WHERE key=?', (email,))
        import_mailbox(database, objects, email, recovery)
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute('SELECT date,seen,recent FROM msgs WHERE mboxId IN '
                '(SELECT id FROM mboxes WHERE uid=(SELECT id FROM users WHERE username=?))',
                (email,)).fetchall(), before)
            restored_flags = connection.execute('SELECT flag FROM flags WHERE mboxId IN '
                '(SELECT id FROM mboxes WHERE uid=(SELECT id FROM users WHERE username=?)) '
                'ORDER BY flag', (email,)).fetchall()
            self.assertEqual(restored_flags, flags)
        fixture.command(('start', fixture.identifier))
        fixture.refresh_ports()
        new_password = 'new recovered mailbox password 24'
        self.assertEqual(fixture.maddy.change(('creds', 'create', email), new_password).code, 0)
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(fixture, email, MAILBOX_PASSWORD)
        with imap_login(fixture, email, new_password) as connection:
            self.assertEqual(connection.select('Saved')[0], 'OK')
            self.assertEqual(connection.search(None, 'ALL')[1], [b'1'])
            response = connection.fetch('1', '(BODY.PEEK[] FLAGS INTERNALDATE)')
            self.assertIn(b'retained-body', response[1][0][1])
            self.assertIn('10-Aug-2024', repr(response))
        with imap_login(fixture, other, MAILBOX_PASSWORD) as connection:
            self.assertIn(b'unrelated-user-remains', bodies(connection))
