import os
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

from backend.local_host import RECOVERY_REQUEST, LocalHost
from helper.deletion import DeletionPaths
from helper.dependencies import HostLocations, build_dependencies
from helper.journal import Journal
from helper.process import CommandResult
from helper.service import HostHelper
from host.preflight import provision_lock
from shared.errors import ServiceError

LOCK_WAIT_SECONDS = 1
OPERATION_TIMEOUT_SECONDS = 5


class FakeMaddy:
    def __init__(self):
        self.stores = {"creds": set(), "imap-acct": set()}

    @property
    def accounts(self):
        return sorted(self.stores["creds"])

    def validate(self):
        return None

    def inventories(self):
        return {"credentials": sorted(self.stores["creds"]),
                "storage": sorted(self.stores["imap-acct"])}

    def change(self, arguments, password=""):
        store, action, address = arguments[0], arguments[1], arguments[-1]
        (self.stores[store].add if action == "create" else self.stores[store].discard)(address)
        return CommandResult(0, "")


class LocalHostTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "data").mkdir()
        state = root / "state"
        state.mkdir(mode=0o700)
        lock = root / "maddy.lock"
        provision_lock(lock, os.getuid())
        Journal.provision(state / "state.db")
        record = {
            "inputs": {
                "lock_wait_seconds": LOCK_WAIT_SECONDS,
                "operation_timeout_seconds": OPERATION_TIMEOUT_SECONDS,
            },
            "maddy_data_directory": str(root / "data"),
            "backup_sources": {"dashboard": str(root)},
        }
        paths = DeletionPaths(root / "deployment.json", root / "policy.json", root / "backup.json")
        locations = HostLocations(state, lock, os.getuid(), paths)
        self.maddy = FakeMaddy()
        self.host = LocalHost(HostHelper(build_dependencies(record, self.maddy, locations)))

    def request(self, operation, **values):
        return {"operation": operation, "request_id": str(uuid.uuid4()), **values}

    def test_inventory_comes_from_the_in_process_maddy(self):
        for store in self.maddy.stores.values():
            store.add("a@rsalehin24.me")
        result = self.host.call(self.request("inventory"))
        self.assertEqual(result["credentials"], ["a@rsalehin24.me"])
        self.assertEqual(result["storage"], ["a@rsalehin24.me"])

    def test_changes_trigger_one_forwarding_sync_and_reads_do_not(self):
        synced = []
        self.host.helper.dependencies = replace(
            self.host.helper.dependencies, forwarding_sync=lambda: synced.append(True))
        self.host.call(self.request("inventory"))
        self.assertEqual(synced, [])
        self.host.call(self.request("create", email="new@rsalehin24.me",
                                    password="Local-host-pass-1!"))
        self.assertEqual(synced, [True])

    def test_create_then_lookup_by_request_id_uses_the_helpers_journal(self):
        request = self.request("create", email="new@rsalehin24.me", password="Local-host-pass-1!")
        self.assertEqual(self.host.call(request)["state"], "complete")
        self.assertEqual(self.maddy.accounts, ["new@rsalehin24.me"])
        outcome = self.host.call({"operation": "outcome", "request_id": request["request_id"]})
        self.assertEqual(outcome["outcome"]["email"], "new@rsalehin24.me")

    def test_invalid_requests_and_disabled_deletion_are_helper_errors(self):
        with self.assertRaises(ServiceError):
            self.host.call({"operation": "create", "request_id": "not-a-uuid"})
        email = "x@rsalehin24.me"
        deletion = self.request(
            "delete", email=email, confirmation=email, acknowledged=True, cutoff="synthetic"
        )
        # The missing host record is an OSError inside; callers only ever see a safe 503.
        with self.assertRaisesRegex(ServiceError, "helper_unavailable"):
            self.host.call(deletion)


class FakeHelper:
    def __init__(self, response):
        self.response = response

    def handle(self, request):
        return self.response


class RecoveryWakeTests(unittest.TestCase):
    """Queued recovery work wakes the root worker through its own trigger file."""

    def host(self, response, state):
        return LocalHost(FakeHelper(response), state)

    def call(self, host, operation):
        return host.call({"operation": operation, "request_id": str(uuid.uuid4())})

    def test_a_queued_recovery_job_writes_the_trigger_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.call(self.host({"state": "queued"}, state), "recovery_policy")
            self.assertTrue((state / RECOVERY_REQUEST).is_file())

    def test_other_results_and_operations_leave_the_trigger_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.call(self.host({"state": "complete"}, state), "recovery_policy")
            self.call(self.host({"state": "queued"}, state), "inventory")
            self.assertFalse((state / RECOVERY_REQUEST).exists())

    def test_a_failed_wake_does_not_fail_a_queued_job(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "absent"
            result = self.call(self.host({"state": "queued"}, missing), "recovery_policy")
            self.assertEqual(result, {"state": "queued"})

