import sqlite3
import unittest

from host.mailbox_store import linked_body_keys


class LinkedBodyKeysTests(unittest.TestCase):
    def test_a_key_row_whose_mailbox_is_gone_is_not_expected_to_have_a_file(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            "CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT);"
            "CREATE TABLE extKeys(id TEXT PRIMARY KEY, uid INTEGER, refs INTEGER);"
            "INSERT INTO users VALUES(1,'a@rsalehin24.me');"
            "INSERT INTO extKeys VALUES('kept',1,1),('orphan',99,1);"
        )
        self.assertEqual(linked_body_keys(connection), {"kept"})


if __name__ == "__main__":
    unittest.main()
