import hashlib
import imaplib
import os
import re
import select
import sqlite3
import stat
import subprocess
import unittest
import uuid
from contextlib import closing

from helper.maddy import LAUNCHER
from helper.protocol import HelperRequest
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class PermissionsAndSessionsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def create(self):
        email = 'permissions-session@rsalehin24.me'
        outcome = self.fixture.helper.handle(
            HelperRequest('create', str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(outcome['state'], 'complete')
        return email

    def body_path(self, marker):
        return next(path for path in self.fixture.data.rglob('*')
                    if path.is_file() and not path.name.startswith(('credentials.db', 'imapsql.db'))
                    and marker in path.read_bytes())

    def test_host_service_uid_and_nobody_cannot_read_real_databases_or_delivered_messages(self):
        email = self.create()
        marker = b'host-permission-fixture-body'
        deliver(self.fixture, email, marker.decode())
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            self.assertIn(marker, bodies(connection))
        paths = (self.fixture.data / 'credentials.db', self.fixture.data / 'imapsql.db',
                 self.body_path(marker))
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            for identity in (10001, 65534):
                self.assert_host_read_denied((identity, path.relative_to(self.fixture.root)))

    def assert_host_read_denied(self, values):
        identity, relative = values
        # Bypass unrelated fixture ancestors so the data-directory permissions are tested.
        parent = os.open(self.fixture.root, os.O_RDONLY | os.O_DIRECTORY)
        process = os.fork()
        if process == 0:
            self.denied_child((identity, relative), parent)
        os.close(parent)
        _, status = os.waitpid(process, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)

    @staticmethod
    def denied_child(values, parent):
        identity, relative = values
        try:
            os.setgroups([])
            os.setgid(identity)
            os.setuid(identity)
        except OSError:
            os._exit(2)
        if os.geteuid() != identity or os.getegid() != identity or os.getgroups():
            os._exit(2)
        try:
            descriptor = os.open(relative, os.O_RDONLY, dir_fd=parent)
        except PermissionError:
            os._exit(0)
        except OSError:
            os._exit(2)
        os.close(descriptor)
        os._exit(1)

    def test_host_umask_does_not_control_an_unwrapped_container_exec(self):
        previous = os.umask(0o077)
        try:
            response = self.fixture.command(('exec', 'maddy', '/bin/sh', '-c',
                                             'umask; : > /data/raw-umask-fixture'))
        finally:
            os.umask(previous)
        self.assertEqual(int(response.stdout.strip(), 8), 0o022)
        self.assertEqual(stat.S_IMODE((self.fixture.data / 'raw-umask-fixture').stat().st_mode),
                         0o644)

    def test_daemon_and_a_real_waiting_cli_process_both_have_umask_0077(self):
        process = subprocess.Popen(
            (self.fixture.docker, 'exec', '-i', self.fixture.container, LAUNCHER,
             'creds', 'create', 'cli-umask@rsalehin24.me'),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.addCleanup(self.close_process, process)
        self.assertTrue(select.select([process.stdout], [], [], 10)[0])
        script = ('for p in /proc/[0-9]*; do '
                  'if [ "$(cat "$p/comm" 2>/dev/null)" = maddy ]; then cat "$p/status"; fi; done')
        result = self.fixture.command(('exec', 'maddy', '/bin/sh', '-c', script))
        masks = re.findall(r'^Umask:\s+([0-7]+)$', result.stdout, re.MULTILINE)
        self.assertGreaterEqual(len(masks), 2)
        self.assertEqual({int(value, 8) for value in masks}, {0o077})
        output, _ = process.communicate((MAILBOX_PASSWORD + '\n').encode() * 2, timeout=30)
        self.assertEqual(process.returncode, 0)
        self.assertTrue(MAILBOX_PASSWORD.encode() not in output,
                        'CLI echoed protected fixture input')

    @staticmethod
    def close_process(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout):
            if stream is not None and not stream.closed:
                stream.close()

    def test_delivery_imap_writes_and_recreated_wal_files_remain_restricted(self):
        email = self.create()
        with closing(sqlite3.connect(self.fixture.data / 'credentials.db')) as database:
            self.assertEqual(database.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        deliver(self.fixture, email, 'permission-write-body')
        self.assert_imap_writes(email)
        self.assert_data_modes()
        before = self.wal_files()
        self.assertEqual({path.name for path in before},
                         {'credentials.db-wal', 'credentials.db-shm'})
        self.fixture.command(('stop', self.fixture.identifier))
        with closing(sqlite3.connect(self.fixture.data / 'credentials.db')) as database:
            self.assertEqual(database.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0], 0)
        self.assertFalse(any(path.exists() for path in before))
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        self.assert_imap_writes(email)
        self.assertEqual(len(self.wal_files()), 2)
        self.assert_data_modes()

    def wal_files(self):
        return [path for path in self.fixture.data.glob('credentials.db-*')
                if path.suffix in {'.db-wal', '.db-shm'}]

    def assert_imap_writes(self, email):
        with imap_login(self.fixture, email, MAILBOX_PASSWORD) as connection:
            message = b'Subject: Permission fixture\r\n\r\nnew body'
            self.assertEqual(connection.append('INBOX', None, None, message)[0], 'OK')
            self.assertIn(b'permission-write-body', bodies(connection))
            self.assertEqual(connection.store('1', '+FLAGS', '\\Seen')[0], 'OK')

    def assert_data_modes(self):
        self.assertEqual(stat.S_IMODE(self.fixture.data.stat().st_mode), 0o700)
        for path in self.fixture.data.rglob('*'):
            self.assertFalse(path.is_symlink())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600,
                             str(path.relative_to(self.fixture.data)))

    def test_credential_removal_keeps_old_session_until_restart_without_deleting_mail(self):
        email = self.create()
        marker = b'explicit-disconnect-preserved-body'
        deliver(self.fixture, email, marker.decode())
        connection = imap_login(self.fixture, email, MAILBOX_PASSWORD)
        self.addCleanup(connection.shutdown)
        self.assertIn(marker, bodies(connection))
        body = self.body_path(marker)
        digest = hashlib.sha256(body.read_bytes()).hexdigest()
        result = self.fixture.maddy.change(('creds', 'remove', '--yes', email))
        self.assertEqual(result.code, 0)
        self.assertEqual(connection.noop()[0], 'OK')
        self.assertIn(marker, bodies(connection))
        self.assert_new_login_denied(email)
        self.fixture.command(('restart', self.fixture.identifier))
        self.fixture.refresh_ports()
        self.fixture._wait()
        with self.assertRaises((imaplib.IMAP4.abort, OSError)):
            connection.noop()
        self.assert_new_login_denied(email)
        self.assertEqual(hashlib.sha256(body.read_bytes()).hexdigest(), digest)
        self.assertIn(email, self.fixture.maddy.inventories()['storage'])

    def assert_new_login_denied(self, email):
        with self.assertRaises(imaplib.IMAP4.error):
            imap_login(self.fixture, email, MAILBOX_PASSWORD)
