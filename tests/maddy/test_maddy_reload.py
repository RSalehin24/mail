import smtplib
import unittest

from helper.submission import DockerRestarter
from tests.maddy_support import RealMaddyFixture


class AlwaysValid:
    def verify(self):
        return True


class MaddyReloadTests(unittest.TestCase):
    def started_at(self, fixture):
        return fixture.command(
            ("inspect", "-f", "{{.State.Running}} {{.State.StartedAt}}", fixture.identifier)
        ).stdout.strip()

    def test_a_reload_keeps_the_container_running_and_mail_reachable(self):
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        before = self.started_at(fixture)
        DockerRestarter(AlwaysValid(), container=fixture.identifier).restart()
        self.assertEqual(self.started_at(fixture), before)
        with smtplib.SMTP("127.0.0.1", fixture.smtp_port, timeout=8) as smtp:
            self.assertEqual(smtp.ehlo("probe.example")[0], 250)
        logs = fixture.command(("logs", fixture.identifier))
        self.assertIn("old server stopped", logs.stdout + logs.stderr)


if __name__ == "__main__":
    unittest.main()
