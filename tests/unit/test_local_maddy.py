import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helper.maddy import LocalMaddy
from shared.errors import ServiceError

CLI_VERSION = "0.9.5"
OPERATION_TIMEOUT_SECONDS = 5
# Stand-in for the Maddy binary: records argv and stdin, then prints a scripted reply.
STUB = """#!/bin/sh
printf '%s\\n' "$@" > "$STUB_DIRECTORY/argv"
printf '%s %s\\n' "$MADDY_DOMAIN" "$MADDY_HOSTNAME" "$HOME" > "$STUB_DIRECTORY/environment"
cat > "$STUB_DIRECTORY/stdin"
cat "$STUB_DIRECTORY/reply"
if [ -f "$STUB_DIRECTORY/errors" ]; then cat "$STUB_DIRECTORY/errors" >&2; fi
"""


class LocalMaddyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        self.data.mkdir()
        for name in ("credentials.db", "imapsql.db"):
            (self.data / name).touch()
        self.binary = self.root / "maddy"
        # The stub reads its scratch directory from a path baked into the script.
        self.binary.write_text(STUB.replace("$STUB_DIRECTORY", str(self.root)))
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.config = self.root / "maddy.conf"
        self.maddy = LocalMaddy(
            self.binary, self.config, self.data, CLI_VERSION, OPERATION_TIMEOUT_SECONDS
        )

    def reply(self, text):
        (self.root / "reply").write_text(text)

    def test_commands_run_the_binary_with_the_config_and_send_the_password_twice(self):
        self.reply("")
        result = self.maddy.change(("creds", "create", "a@rsalehin24.me"), "secret-value")
        self.assertEqual(result.code, 0)
        self.assertEqual(
            (self.root / "argv").read_text().split(),
            ["-config", str(self.config), "creds", "create", "a@rsalehin24.me"],
        )
        self.assertEqual((self.root / "stdin").read_text(), "secret-value\nsecret-value\n")

    def test_the_config_variables_reach_the_command_and_nothing_else_does(self):
        self.reply("")
        variables = {"MADDY_DOMAIN": "example.test", "MADDY_HOSTNAME": "mail.example.test",
                     "HOME": "/leaked"}
        with mock.patch.dict("os.environ", variables):
            self.maddy.change(("version",))
        first, second = (self.root / "environment").read_text().splitlines()
        self.assertEqual(first, "example.test mail.example.test")
        self.assertEqual(second.strip(), "")  # HOME did not reach the command

    def test_inventories_parse_accounts_and_the_empty_marker(self):
        self.reply("b@rsalehin24.me\na@rsalehin24.me\n")
        self.assertEqual(
            self.maddy.inventories(),
            {"credentials": ["a@rsalehin24.me", "b@rsalehin24.me"],
             "storage": ["a@rsalehin24.me", "b@rsalehin24.me"]},
        )
        self.reply("No users.\n")
        self.assertEqual(self.maddy.inventories(), {"credentials": [], "storage": []})

    def test_log_lines_on_stderr_are_not_read_as_accounts(self):
        # The ACME loader logs certificate maintenance whenever the CLI loads the config.
        self.reply("a@rsalehin24.me\n")
        (self.root / "errors").write_text(
            'tls.loader.acme/maintenance: started background certificate maintenance\t{}\n'
        )
        self.assertEqual(self.maddy.inventories()["credentials"], ["a@rsalehin24.me"])

    def test_an_error_logged_on_stderr_with_exit_code_zero_is_still_an_error(self):
        self.reply("")
        (self.root / "errors").write_text('app.Run failed\t{"reason":"bad config"}\n')
        with self.assertRaises(ServiceError) as caught:
            self.maddy.inventories()
        self.assertEqual(caught.exception.code, "inventory_unavailable")

    def test_an_error_reported_with_exit_code_zero_is_not_an_inventory(self):
        self.reply('app.Run failed\t{"reason":"mkdir /run/maddy: permission denied"}\n')
        with self.assertRaises(ServiceError) as caught:
            self.maddy.inventories()
        self.assertEqual(caught.exception.code, "inventory_unavailable")

    def test_validate_accepts_the_pinned_version_and_rejects_others(self):
        self.reply("0.9.5 linux/amd64 go1.23.12\n")
        self.maddy.validate()
        self.reply("0.9.4 linux/amd64 go1.23.12\n")
        with self.assertRaises(ServiceError) as caught:
            self.maddy.validate()
        self.assertEqual(caught.exception.code, "maddy_version_mismatch")

    def test_validate_requires_the_binary_and_both_databases(self):
        self.reply("0.9.5 linux/amd64\n")
        (self.data / "imapsql.db").unlink()
        with self.assertRaises(ServiceError) as caught:
            self.maddy.validate()
        self.assertEqual(caught.exception.code, "maddy_storage_missing")
        (self.data / "imapsql.db").touch()
        self.binary.unlink()
        with self.assertRaises(ServiceError) as caught:
            self.maddy.validate()
        self.assertEqual(caught.exception.code, "maddy_unavailable")
