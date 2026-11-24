import os
import smtplib
import tempfile
import time
import unittest

from tests.maddy_support import RealMaddyFixture

RELOADED = "old server stopped"
WAIT_SECONDS = 10


class MaddySelfReloadTests(unittest.TestCase):
    """Replacing submission-relay.conf in /data makes the launcher reload Maddy in place."""

    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def started_at(self):
        return self.fixture.command(
            ("inspect", "-f", "{{.State.StartedAt}}", self.fixture.identifier)
        ).stdout.strip()

    def reloads(self):
        logs = self.fixture.command(("logs", self.fixture.identifier))
        return (logs.stdout + logs.stderr).count(RELOADED)

    def replace(self, name):
        descriptor, temporary = tempfile.mkstemp(prefix=".fixture-", dir=self.fixture.data)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("# synthetic\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.fixture.data / name)

    def wait_for_reloads(self, count):
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            if self.reloads() >= count:
                return
            time.sleep(0.2)
        self.fail(f"Maddy did not reload {count} time(s) within {WAIT_SECONDS} s")

    def test_the_relay_include_reloads_maddy_without_restarting_the_container(self):
        before = self.started_at()
        self.replace("submission-relay.conf")
        self.wait_for_reloads(1)
        self.assertEqual(self.started_at(), before)
        with smtplib.SMTP("127.0.0.1", self.fixture.smtp_port, timeout=8) as smtp:
            self.assertEqual(smtp.ehlo("probe.example")[0], 250)

    def test_other_files_do_not_reload_maddy(self):
        self.replace("submission-relay.conf")
        self.wait_for_reloads(1)
        self.replace("unrelated.conf")
        time.sleep(3)
        self.assertEqual(self.reloads(), 1)


if __name__ == "__main__":
    unittest.main()
