import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from backend.local_host import LocalHost
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
        Journal.provision(state / "journal.db")
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

    def test_create_then_lookup_by_request_id_uses_the_helpers_journal(self):
        request = self.request("create", email="new@rsalehin24.me", password="Local-host-pass-1!")
        self.assertEqual(self.host.call(request)["state"], "complete")
        self.assertEqual(self.maddy.accounts, ["new@rsalehin24.me"])
        outcome = self.host.call({"operation": "outcome", "request_id": request["request_id"]})
        self.assertEqual(outcome["outcome"]["email"], "new@rsalehin24.me")

    def test_invalid_requests_and_disabled_deletion_are_helper_errors(self):
        with self.assertRaises(ServiceError):
            self.host.call({"operation": "create", "request_id": "not-a-uuid"})
        deletion = self.request("delete", email="x@rsalehin24.me", recovery=None)
        with self.assertRaises((ServiceError, FileNotFoundError, json.JSONDecodeError)):
            self.host.call(deletion)
