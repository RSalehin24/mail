"""The launcher's inotifyd handler branch, run with a shell function standing in for kill."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / "host" / "maddy-launcher"
# kill is a shell builtin, so a fake on PATH would never run; a function shadows the builtin.
# Sourcing keeps the launcher's own `set -eu` and handler code, with the arguments as $1..$3.
HARNESS = 'kill() { echo "$@" >> "$KILL_RECORD"; }; . "$LAUNCHER"'


class ReloadHandlerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.record = Path(directory.name) / "kill.log"

    def handle(self, *arguments):
        environment = {**os.environ, "MAIL_ADMIN_RELOAD": "1",
                       "KILL_RECORD": str(self.record), "LAUNCHER": str(LAUNCHER)}
        result = subprocess.run(["sh", "-c", HARNESS, "launcher", *arguments], env=environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.record.read_text() if self.record.exists() else ""

    def test_the_relay_include_moved_into_place_signals_maddy_once(self):
        self.assertEqual(self.handle("y", "/data", "submission-relay.conf"), "-USR2 1\n")

    def test_other_files_and_missing_names_signal_nothing(self):
        self.assertEqual(self.handle("y", "/data", "imapsql.db"), "")
        self.assertEqual(self.handle("y", "/data"), "")
        self.assertEqual(self.handle(), "")


if __name__ == "__main__":
    unittest.main()
