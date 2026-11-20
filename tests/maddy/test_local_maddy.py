import imaplib
import shutil
import tempfile
import unittest
from pathlib import Path

from helper.maddy import LocalMaddy
from tests.maddy_support import RealMaddyFixture, imap_login

CLI_VERSION = "0.9.5+postmaster-fixture.1"
OPERATION_TIMEOUT_SECONDS = 20
STATE_DIRECTORY = Path("/data")  # The fixture configuration names this state_dir.
PASSWORD = "Local-maddy-password-1!"
CHANGED_PASSWORD = "Local-maddy-password-2!"


class LocalMaddyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        binary = Path(scratch.name) / "maddy"
        container = self.fixture.command(("create", self.fixture.image_reference())).stdout.strip()
        try:
            self.fixture.command(("cp", container + ":/bin/maddy", str(binary)))
        finally:
            self.fixture.command(("rm", container))
        self.assertFalse(STATE_DIRECTORY.exists(), "Refusing to replace an existing /data")
        # The CLI loads the certificate named in the configuration, so it needs the TLS files too.
        shutil.copytree(self.fixture.tls, self.fixture.data / "tls", dirs_exist_ok=True)
        STATE_DIRECTORY.symlink_to(self.fixture.data)
        self.addCleanup(STATE_DIRECTORY.unlink)
        self.maddy = LocalMaddy(
            binary, self.fixture.configuration, self.fixture.data, CLI_VERSION,
            OPERATION_TIMEOUT_SECONDS,
        )

    def create(self, email):
        for arguments in (("creds", "create", email), ("imap-acct", "create", email)):
            response = self.maddy.change(arguments, PASSWORD if arguments[0] == "creds" else "")
            self.assertEqual(response.code, 0, response.output)

    def login(self, email, password):
        imap_login(self.fixture, email, password).logout()

    def test_local_commands_manage_accounts_the_running_server_serves(self):
        self.maddy.validate()
        email = "local@rsalehin24.me"
        self.create(email)
        self.assertEqual(self.maddy.inventories(), {"credentials": [email], "storage": [email]})
        self.login(email, PASSWORD)
        self.maddy.change(("creds", "password", email), CHANGED_PASSWORD)
        self.login(email, CHANGED_PASSWORD)
        with self.assertRaises(imaplib.IMAP4.error):
            self.login(email, PASSWORD)
        self.maddy.change(("creds", "remove", "--yes", email))
        self.maddy.change(("imap-acct", "remove", "--yes", email))
        self.assertEqual(self.maddy.inventories(), {"credentials": [], "storage": []})
        with self.assertRaises(imaplib.IMAP4.error):
            self.login(email, CHANGED_PASSWORD)
