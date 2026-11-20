"""File handshake between the dashboard and the root Maddy restart worker, with fakes only."""

import json
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from helper import maddy_restart as app_side
from helper.maddy_restart import REQUEST_NAME, RESULT_NAME, FileRestarter, LocalVerifier
from helper.process import CommandResult
from helper.submission import RelayActions, SubmissionControl
from host import maddy_restart as worker
from shared.errors import ServiceError
from shared.files import atomic_json

OWNER = os.geteuid()


def answer_when_requested(state, outcome):
    """Plays the root worker: waits for the request, then writes the chosen answer."""
    def run():
        request = state / REQUEST_NAME
        while not request.exists():
            threading.Event().wait(0.005)
        atomic_json(state / RESULT_NAME, outcome(json.loads(request.read_text())["id"]))
    thread = threading.Thread(target=run)
    thread.start()
    return thread


class HandshakeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.restarter = FileRestarter(self.state, OWNER, timeout=0.5)
        patcher = patch.object(app_side, "POLL_INTERVAL_SECONDS", 0.005)
        patcher.start()
        self.addCleanup(patcher.stop)

    def restart_with(self, outcome):
        thread = answer_when_requested(self.state, outcome)
        self.addCleanup(thread.join)
        self.restarter.restart()

    def test_matching_successful_result_completes(self):
        self.restart_with(lambda request_id: {"id": request_id, "ok": True})
        request = self.state / REQUEST_NAME
        self.assertEqual(request.stat().st_mode & 0o777, 0o600)
        self.assertEqual(request.stat().st_uid, OWNER)

    def test_failed_result_is_a_restart_failure(self):
        with self.assertRaisesRegex(ServiceError, "submission_restart_failed"):
            self.restart_with(lambda request_id: {"id": request_id, "ok": False})

    def test_result_for_another_request_times_out(self):
        self.restarter.timeout = 0.1
        with self.assertRaisesRegex(ServiceError, "submission_restart_failed"):
            self.restart_with(lambda _: {"id": str(uuid.uuid4()), "ok": True})

    def test_missing_result_times_out(self):
        self.restarter.timeout = 0.05
        with self.assertRaisesRegex(ServiceError, "submission_restart_failed"):
            self.restarter.restart()

    def test_stale_success_from_an_earlier_request_is_not_accepted(self):
        atomic_json(self.state / RESULT_NAME, {"id": str(uuid.uuid4()), "ok": True})
        self.restarter.timeout = 0.05
        with self.assertRaisesRegex(ServiceError, "submission_restart_failed"):
            self.restarter.restart()

    def test_result_with_unsafe_owner_is_ignored(self):
        self.restarter.owner = OWNER + 1
        self.restarter.timeout = 0.1
        with self.assertRaisesRegex(ServiceError, "submission_restart_failed"):
            self.restart_with(lambda request_id: {"id": request_id, "ok": True})


class WorkerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)
        self.request = self.state / REQUEST_NAME
        self.result = self.state / RESULT_NAME

    def file_request(self):
        request_id = str(uuid.uuid4())
        atomic_json(self.request, {"id": request_id})
        return request_id

    def run_worker(self, restart_error=None):
        with patch.object(worker.DockerRestarter, "restart",
                          side_effect=restart_error) as restart:
            code = worker.process(self.state)
        return code, restart

    def test_valid_request_is_restarted_answered_and_removed(self):
        request_id = self.file_request()
        code, restart = self.run_worker()
        self.assertEqual((code, restart.call_count), (0, 1))
        self.assertEqual(json.loads(self.result.read_text()), {"id": request_id, "ok": True})
        self.assertFalse(self.request.exists())

    def test_result_file_is_handed_to_the_state_owner_before_it_appears(self):
        self.file_request()
        with patch("shared.files.os.fchown") as fchown:
            self.run_worker()
        fchown.assert_called_once()
        self.assertEqual(fchown.call_args.args[1:], (self.state.stat().st_uid, -1))

    def test_failed_restart_writes_a_negative_result(self):
        request_id = self.file_request()
        code, _ = self.run_worker(ServiceError("submission_restart_failed", 503))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.result.read_text()), {"id": request_id, "ok": False})
        self.assertFalse(self.request.exists())

    def test_a_request_is_never_acted_on_twice(self):
        request_id = self.file_request()
        atomic_json(self.result, {"id": request_id, "ok": True})
        _, restart = self.run_worker()
        restart.assert_not_called()
        self.assertFalse(self.request.exists())

    def test_missing_request_does_nothing(self):
        code, restart = self.run_worker()
        self.assertEqual((code, restart.call_count), (0, 0))

    def test_untrusted_requests_are_removed_without_restarting(self):
        cases = {
            "not a uuid": {"id": "x"},
            "no id": {},
            "oversize": {"id": str(uuid.uuid4()), "pad": "x" * worker.MAX_REQUEST_BYTES},
        }
        for name, record in cases.items():
            with self.subTest(name):
                atomic_json(self.request, record)
                code, restart = self.run_worker()
                self.assertEqual((code, restart.call_count), (1, 0))
                self.assertFalse(self.request.exists())
                self.assertFalse(self.result.exists())

    def test_request_owned_by_someone_else_is_rejected(self):
        self.file_request()
        other_owner = type("Stat", (), {"st_uid": OWNER + 1})()
        with patch.object(Path, "stat", return_value=other_owner):
            code, restart = self.run_worker()
        self.assertEqual((code, restart.call_count), (1, 0))

    def test_symlinked_request_is_rejected(self):
        target = self.state / "elsewhere.json"
        atomic_json(target, {"id": str(uuid.uuid4())})
        self.request.symlink_to(target)
        code, restart = self.run_worker()
        self.assertEqual((code, restart.call_count), (1, 0))
        self.assertFalse(self.request.is_symlink())
        self.assertTrue(target.exists())


class FakeVerifier:
    def __init__(self, valid):
        self.valid = valid

    def verify(self):
        return self.valid


class FakeRestarter:
    def __init__(self, failures):
        self.calls, self.failures = 0, failures

    def restart(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ServiceError("submission_restart_failed", 503)


class RelayRollbackTests(unittest.TestCase):
    def synchronize(self, valid, failures):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "submission-relay.conf"
        path.write_bytes(b"previous")
        path.chmod(0o600)
        restarter = FakeRestarter(failures)
        actions = RelayActions(FakeVerifier(valid), restarter)
        read = lambda path, owner: path.read_bytes()  # noqa: E731 -- owner is unused here
        with patch("helper.submission.protected_bytes", side_effect=read):
            control = SubmissionControl(Path(directory.name), None, actions)
            with self.assertRaises(ServiceError) as raised:
                control.synchronize({"username": "u", "credential": "secret-1", "revision": 1})
        return path, restarter, raised.exception

    def test_invalid_configuration_restores_previous_and_restarts(self):
        path, restarter, error = self.synchronize(valid=False, failures=0)
        self.assertEqual(error.code, "submission_synchronization_failed")
        self.assertEqual(path.read_bytes(), b"previous")
        self.assertEqual(restarter.calls, 1)

    def test_restart_failure_attempts_rollback(self):
        path, restarter, error = self.synchronize(valid=True, failures=1)
        self.assertEqual(error.code, "submission_synchronization_failed")
        self.assertEqual(path.read_bytes(), b"previous")
        self.assertEqual(restarter.calls, 2)

    def test_failed_rollback_is_reported(self):
        _, restarter, error = self.synchronize(valid=True, failures=2)
        self.assertEqual(error.code, "submission_rollback_failed")
        self.assertEqual(restarter.calls, 2)


class LocalVerifierTests(unittest.TestCase):
    def verify(self, code, output):
        class Maddy:
            def change(self, arguments, password=""):
                self.arguments = arguments
                return CommandResult(code, output)

        maddy = Maddy()
        return LocalVerifier(maddy).verify(), maddy.arguments

    def test_success_needs_zero_exit_and_no_reported_failure(self):
        self.assertEqual(self.verify(0, ""), (True, ("verify-config",)))
        self.assertFalse(self.verify(1, "")[0])
        self.assertFalse(self.verify(0, "app.Run failed\n")[0])
