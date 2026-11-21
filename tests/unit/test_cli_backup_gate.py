import unittest
from unittest import mock

from host import cli
from shared.errors import ServiceError


class BackupWithoutRolloutGateTests(unittest.TestCase):
    """The nightly backup no longer needs the retired rollout checkpoint records."""

    def test_backup_and_verify_run_without_reading_checkpoints(self):
        for action, method in (("backup", "run"), ("backup-verify", "verify")):
            with (
                self.subTest(action=action),
                mock.patch.object(cli, "require_checkpoint", side_effect=ServiceError("gone", 409)),
                mock.patch.object(cli, "BackupOperations") as operations,
                mock.patch.object(cli.Path, "is_file", return_value=False),
            ):
                cli.run(action, mock.MagicMock())
                getattr(operations.return_value, method).assert_called_once_with()
