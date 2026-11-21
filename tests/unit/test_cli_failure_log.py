import contextlib
import io
import unittest
from unittest import mock

from host import cli
from shared.errors import ServiceError


class FailureLogTests(unittest.TestCase):
    def run_failing(self, error):
        context = mock.MagicMock()
        stderr = io.StringIO()
        with (
            mock.patch("sys.argv", ["host.cli", "backup"]),
            mock.patch("os.geteuid", return_value=0),
            mock.patch.object(cli.HostContext, "load", return_value=context),
            mock.patch.object(cli, "run", side_effect=error),
            contextlib.redirect_stderr(stderr),
        ):
            code = cli.main()
        return code, stderr.getvalue(), context

    def test_a_failed_action_says_why_and_still_raises_the_alert(self):
        for error, reason in ((ServiceError("repository_locked", 503), "repository_locked"),
                              (OSError("disk gone"), "disk gone")):
            with self.subTest(reason=reason):
                code, text, context = self.run_failing(error)
                self.assertEqual(code, 1)
                self.assertIn(f"backup failed: {type(error).__name__}: {reason}", text)
                context.alerts.observe.assert_called_with("backup", "failed_or_deferred")
