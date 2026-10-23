import io
import json
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from helper.maddy import LAUNCHER, DockerMaddy, cli_reported_error
from helper.process import CommandResult
from helper.protocol import HelperRequest
from helper.server import HelperHandler
from helper.service import HostHelper
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD, HelperFixture


class HelperTests(unittest.TestCase):
    def test_root_deletion_capability_requires_a_mapping_and_exact_true(self):
        from helper.deletion import DeletionGuard, DeletionPaths

        root = Path('/unused-fixture')
        guard = DeletionGuard(DeletionPaths(root / 'deployment', root / 'policy', root / 'backup'),
                              lambda: 0)
        records = ({}, {'inputs': None}, {'inputs': []}, {'inputs': 'invalid'},
                   {'inputs': {}}, {'inputs': {'permanent_deletion_enabled': 1}},
                   {'inputs': {'permanent_deletion_enabled': 'true'}},
                   {'inputs': {'permanent_deletion_enabled': False}})
        for record in records:
            with self.subTest(record=record), patch('helper.deletion.protected_json',
                                                   return_value=record):
                with self.assertRaises(ServiceError) as caught:
                    guard.capability()
                self.assertEqual(caught.exception.code, 'permanent_deletion_disabled')
        enabled = {'inputs': {'permanent_deletion_enabled': True}}
        with patch('helper.deletion.protected_json', return_value=enabled):
            self.assertEqual(guard.capability(), enabled)

    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)

    def test_both_inventories_must_be_absent_for_create(self):
        for store in (self.fixture.maddy.credentials, self.fixture.maddy.storage):
            store.add("orphan@rsalehin24.me")
            with self.assertRaises(ServiceError):
                self.fixture.helper.handle(self.fixture.request("create", "orphan@rsalehin24.me"))
            self.assertEqual(self.fixture.maddy.calls, [])

    def test_lost_socket_response_keeps_the_committed_outcome_and_logs_no_payload(self):
        request = self.fixture.request("create")
        handler = object.__new__(HelperHandler)
        handler.connection = MagicMock()
        handler.server = SimpleNamespace(
            policy=SimpleNamespace(timeout=5), authorized=lambda _: True, helper=self.fixture.helper
        )
        handler.rfile = io.BytesIO(
            json.dumps(
                {
                    "protocol": 1,
                    "operation": request.operation,
                    "request_id": request.request_id,
                    "email": request.email,
                    "password": request.password,
                }
            ).encode()
            + b"\n"
        )
        handler.wfile = MagicMock()
        handler.wfile.write.side_effect = BrokenPipeError("fixture disconnected caller")
        with self.assertLogs("helper.server", level="WARNING") as events:
            handler.handle()
        self.assertEqual(self.fixture.journal.lookup(request.request_id)["state"], "complete")
        self.assertEqual(events.output, ["WARNING:helper.server:helper_response_lost"])

    def test_duplicate_password_request_executes_only_once_and_conflict_fails(self):
        self.fixture.maddy.credentials.add("fixture@rsalehin24.me")
        self.fixture.maddy.storage.add("fixture@rsalehin24.me")
        request = self.fixture.request("password")
        self.assertEqual(self.fixture.helper.handle(request)["state"], "complete")
        self.fixture.helper.handle(request)
        self.assertEqual(len(self.fixture.maddy.calls), 1)
        conflicting = HelperRequest(
            "create", request.request_id, "other@rsalehin24.me", MAILBOX_PASSWORD
        )
        with self.assertRaises(ServiceError):
            self.fixture.helper.handle(conflicting)

    def test_storage_insert_before_error_keeps_credentials_and_blocks_other_writes(self):
        self.fixture.maddy.fail = ("imap-acct", "create")
        request = self.fixture.request("create")
        result = self.fixture.helper.handle(request)
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn(request.email, self.fixture.maddy.credentials)
        self.fixture.helper.handle(request)
        self.assertEqual(len(self.fixture.maddy.calls), 2)
        with self.assertRaises(ServiceError):
            self.fixture.helper.handle(self.fixture.request("password"))

    def test_folder_warning_does_not_claim_complete_provisioning(self):
        self.fixture.maddy.warning = True
        result = self.fixture.helper.handle(self.fixture.request("create"))
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertTrue(any(step["warnings"] for step in result["steps"]))

    def test_native_folder_failure_text_keeps_credentials_and_requires_reconciliation(self):
        original = self.fixture.maddy.change

        def folders(arguments, password=""):
            response = original(arguments, password)
            return (
                CommandResult(0, "Failed to create sent folder: fixture SQL failure")
                if arguments[:2] == ("imap-acct", "create")
                else response
            )

        with patch.object(self.fixture.maddy, "change", side_effect=folders):
            result = self.fixture.helper.handle(self.fixture.request("create"))
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertTrue(result["steps"][-1]["warnings"])
        self.assertIn("fixture@rsalehin24.me", self.fixture.maddy.credentials)

    def test_zero_exit_reported_password_error_is_not_a_success_and_actual_exit_is_preserved(self):
        self.fixture.maddy.credentials.add("fixture@rsalehin24.me")
        self.fixture.maddy.storage.add("fixture@rsalehin24.me")
        response = CommandResult(0, 'app.Run failed\t{"reason":"fixture SQL error"}\n')
        with patch.object(self.fixture.maddy, "change", return_value=response):
            result = self.fixture.helper.handle(self.fixture.request("password"))
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertEqual(result["steps"][0]["exit_code"], 0)
        self.assertTrue(result["steps"][0]["error_reported"])
        self.assertTrue(self.fixture.journal.unresolved())

    def test_zero_exit_inventory_error_is_not_parsed_as_an_account(self):
        maddy = DockerMaddy({"inputs": {"operation_timeout_seconds": 5}})
        response = CommandResult(0, 'app.Run failed\t{"reason":"fixture SQL error"}\n')
        with patch.object(maddy, "change", return_value=response):
            with self.assertRaisesRegex(ServiceError, "inventory_unavailable"):
                maddy.inventories()

    def test_zero_exit_unavailable_update_pipe_is_a_cli_failure_before_inventory_or_mutation(self):
        output = ('Failed to initialize update pipe, do not remove messages from mailboxes '
                  'open by clients: dial unix /run/maddy/sql-fixture.sock: connection refused\n')
        response = CommandResult(0, output + 'fixture@rsalehin24.me\n')
        self.assertTrue(cli_reported_error(response))
        maddy = DockerMaddy({'inputs': {'operation_timeout_seconds': 5}})
        with patch.object(maddy, 'change', return_value=response):
            with self.assertRaisesRegex(ServiceError, 'inventory_unavailable'):
                maddy.inventories()
        self.fixture.maddy.credentials.add('fixture@rsalehin24.me')
        self.fixture.maddy.storage.add('fixture@rsalehin24.me')
        with patch.object(self.fixture.maddy, 'change', return_value=response):
            result = self.fixture.helper.handle(self.fixture.request('password'))
        self.assertEqual(result['state'], 'reconciliation_required')
        self.assertTrue(result['steps'][0]['error_reported'])
        self.assertEqual(result['steps'][0]['exit_code'], 0)

    def test_cli_error_is_recognized_after_a_password_prompt_without_a_newline(self):
        for prefix in ("", "Enter new password: ", "Enter password for new user: "):
            with self.subTest(prompt=prefix):
                response = CommandResult(0, prefix + 'app.Run failed\t{"reason":"SQL"}')
                self.assertTrue(cli_reported_error(response))
        self.assertFalse(cli_reported_error(CommandResult(0, "app.run.failed@rsalehin24.me\n")))

    def test_unknown_completion_survives_helper_recreation(self):
        self.fixture.maddy.unknown = True
        request = self.fixture.request("create")
        with self.assertRaises(ServiceError):
            self.fixture.helper.handle(request)
        restarted = HostHelper(self.fixture.helper.dependencies)
        result = restarted.handle(request)
        self.assertEqual(result["state"], "unknown")
        self.assertEqual(len(self.fixture.maddy.calls), 1)

    def test_partial_storage_deletion_remains_dangerous_even_if_account_reappears(self):
        self.fixture.deletion.enabled = True
        self.fixture.maddy.credentials.add("fixture@rsalehin24.me")
        self.fixture.maddy.storage.add("fixture@rsalehin24.me")
        self.fixture.maddy.fail = ("imap-acct", "remove")
        request = self.fixture.request("delete")
        result = self.fixture.helper.handle(request)
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn("some messages may already", result["warning"])
        self.fixture.maddy.storage.add(request.email)
        self.fixture.helper.handle(request)
        self.assertEqual(len(self.fixture.maddy.calls), 2)

    def test_disabled_deletion_and_missing_password_accounts_do_not_mutate(self):
        for operation in ("delete", "password"):
            with self.assertRaises(ServiceError):
                self.fixture.helper.handle(self.fixture.request(operation))
        self.assertEqual(self.fixture.maddy.calls, [])

    def test_journal_failure_prevents_mutation_command(self):
        with patch.object(self.fixture.journal, "begin", side_effect=ServiceError("full", 503)):
            with self.assertRaises(ServiceError):
                self.fixture.helper.handle(self.fixture.request("create"))
        self.assertEqual(self.fixture.maddy.calls, [])

    def test_fixed_argv_and_password_only_on_stdin(self):
        captured = []
        with patch(
            "helper.maddy.execute",
            side_effect=lambda command: captured.append(command) or CommandResult(0, ""),
        ):
            DockerMaddy({"inputs": {"operation_timeout_seconds": 5}}).change(
                ("creds", "password", "fixture@rsalehin24.me"), MAILBOX_PASSWORD
            )
        command = captured[0]
        self.assertEqual(
            command.arguments[:5], ("/usr/bin/docker", "exec", "-i", "maddy", LAUNCHER)
        )
        self.assertNotIn(MAILBOX_PASSWORD, command.arguments)
        self.assertIn(MAILBOX_PASSWORD.encode(), command.input_bytes)

    def test_real_cli_empty_inventory_message_is_not_an_address(self):
        maddy = DockerMaddy({"inputs": {"operation_timeout_seconds": 5}})
        with patch.object(maddy, "change", return_value=CommandResult(0, "No users.\n")):
            self.assertEqual(maddy.inventories(), {"credentials": [], "storage": []})

    def test_fixture_readiness_requires_trusted_tls_and_imap_response(self):
        from tests.maddy_support import RealMaddyFixture

        fixture = RealMaddyFixture.__new__(RealMaddyFixture)
        fixture.certificates = SimpleNamespace(trust="fixture.pem")
        fixture.imap_port = 993
        ready = MagicMock()
        ready.__enter__.return_value.noop.return_value = ("OK", [])
        with (
            patch("tests.maddy_support.ssl.create_default_context"),
            patch(
                "tests.maddy_support.imaplib.IMAP4_SSL",
                side_effect=[OSError("TLS listener starting"), ready],
            ) as connection,
            patch("tests.maddy_support.time.sleep"),
        ):
            fixture._wait()
        self.assertEqual(connection.call_count, 2)

    def test_protocol_unknown_fields_types_and_injection_fail(self):
        base = {"protocol": 1, "operation": "inventory", "request_id": str(uuid.uuid4())}
        for extra in ({"path": "/data"}, {"command": "id"}, {"protocol": True}, {"operation": []}):
            with self.assertRaises(ServiceError):
                HelperRequest.parse({**base, **extra})
        with self.assertRaises(ServiceError):
            HelperRequest.parse(
                {
                    **base,
                    "operation": "create",
                    "email": "--help@rsalehin24.me",
                    "password": MAILBOX_PASSWORD,
                }
            )
