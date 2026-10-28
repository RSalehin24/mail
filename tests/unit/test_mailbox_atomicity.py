import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from host.mailbox_transaction import mailbox_transaction


class MailboxAtomicityTests(unittest.TestCase):
    def test_killed_process_rolls_back_every_attached_database_and_commit_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = {name: Path(directory) / (name + '.db') for name in
                     ('main', 'credentials', 'dashboard', 'incarnations', 'ledger')}
            for path in paths.values():
                with sqlite3.connect(path) as connection:
                    connection.execute('CREATE TABLE state(value INTEGER)')
                    connection.execute('INSERT INTO state VALUES(0)')
            for commit in (False, True):
                pid = os.fork()
                if pid == 0:
                    with mailbox_transaction(paths) as connection:
                        for name in paths:
                            connection.execute(f'UPDATE {name}.state SET value=1')  # noqa: S608 -- fixed schemas
                        if not commit:
                            os._exit(91)
                    os._exit(0)
                _, status = os.waitpid(pid, 0)
                self.assertEqual(os.waitstatus_to_exitcode(status), 0 if commit else 91)
                for path in paths.values():
                    with sqlite3.connect(path) as connection:
                        value = connection.execute('SELECT value FROM state').fetchone()[0]
                        self.assertEqual(value, int(commit))
