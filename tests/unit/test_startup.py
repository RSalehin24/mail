import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.__main__ import main
from shared.errors import ServiceError


class StartupTests(unittest.TestCase):
    def test_missing_database_is_rejected_before_loading_or_starting_http_server(self):
        server = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": server}),
            patch("backend.__main__.os.umask"),
            patch("backend.__main__.Settings.from_environment"),
            patch(
                "backend.__main__.require_existing_database",
                side_effect=ServiceError("installation_state_missing", 503),
            ) as validate,
        ):
            with self.assertRaises(ServiceError) as caught:
                main()
        validate.assert_called_once_with(Path("/app/data/admin_data.db"))
        self.assertEqual(caught.exception.code, "installation_state_missing")
        server.run.assert_not_called()

    def test_maintenance_policy_rejects_a_restart_before_the_http_server_is_started(self):
        server = MagicMock()
        with (
            patch.dict("sys.modules", {"uvicorn": server}),
            patch("backend.__main__.os.umask"),
            patch("backend.__main__.Settings.from_environment"),
            patch("backend.__main__.require_existing_database"),
            patch(
                "backend.__main__.RuntimePolicy.require_running",
                side_effect=ServiceError("operator_maintenance", 503),
            ),
        ):
            with self.assertRaisesRegex(ServiceError, "operator_maintenance"):
                main()
        server.run.assert_not_called()
