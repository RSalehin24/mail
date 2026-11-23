import tempfile
import unittest
import uuid
from pathlib import Path

from helper.backup_control import BackupControl, validate_backup
from helper.protocol import HelperRequest
from shared.errors import ServiceError

SNAPSHOT = "a" * 64


class BackupControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = BackupControl(Path(self.directory.name))
        with self.control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (SNAPSHOT, 1, 1))

    def begin(self, kind="backup", snapshot="", previous=""):
        identifier = str(uuid.uuid4())
        self.control.begin((identifier, kind, snapshot, previous), 1000)
        return identifier

    def test_one_unfinished_operation_blocks_the_next_until_it_is_settled(self):
        first = self.begin()
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.begin()
        self.control.update(first, "failed", "capture", 1001, "disk_full")
        self.assertEqual(BackupControl(Path(self.directory.name)).job(first)["error"], "disk_full")
        self.assertTrue(self.begin())

    def test_interrupted_operation_requires_reconciliation_before_anything_else(self):
        original = self.begin()
        self.control.interrupt_running()
        self.assertEqual(self.control.job(original)["state"], "reconciliation_required")
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.begin()
        reconcile = self.begin("reconcile", previous=original)
        self.assertEqual(self.control.job(reconcile)["state"], "running")
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.begin("reconcile", previous=original)

    def test_reconcile_only_applies_to_an_operation_that_needs_it(self):
        original = self.begin()
        self.control.update(original, "complete", "complete", 1001)
        with self.assertRaisesRegex(ServiceError, "backup_reconciliation_not_required"):
            self.begin("reconcile", previous=original)

    def test_restore_needs_a_known_snapshot_and_its_completed_preflight(self):
        with self.assertRaisesRegex(ServiceError, "backup_snapshot_unavailable"):
            self.begin("preflight", "b" * 64)
        prepared = self.begin("preflight", SNAPSHOT)
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.begin("restore", SNAPSHOT, prepared)
        self.control.update(prepared, "failed", "verifying_snapshot", 1001)
        with self.assertRaisesRegex(ServiceError, "backup_preflight_required"):
            self.begin("restore", SNAPSHOT, prepared)
        self.control.update(prepared, "complete", "complete", 1002)
        restore = self.begin("restore", SNAPSHOT, prepared)
        self.assertEqual(self.control.job(restore, private=True)["payload"], {"previous": prepared})

    def test_unknown_kind_is_refused(self):
        with self.assertRaisesRegex(ServiceError, "invalid_backup_operation"):
            self.begin("refresh")

    def test_catalog_numbered_pages_clamp_and_preserve_observation(self):
        with self.control.connect() as connection:
            connection.executemany(
                "INSERT INTO snapshots VALUES(?,?,?)", [(f"{i:064x}", i, 100) for i in range(125)]
            )
            connection.execute("INSERT INTO observations VALUES(1,100,?)", ("",))
        result = self.control.listing({"kind": "snapshots", "page": 99, "page_size": 10})
        self.assertEqual((result["page"], result["total"], len(result["items"])), (13, 126, 6))
        self.assertEqual(result["observation"]["observed"], 100)

    def test_closed_protocol_is_read_only_and_refuses_paths_and_invalid_pages(self):
        payload = {
            "protocol": 1,
            "operation": "backup_list",
            "request_id": str(uuid.uuid4()),
            "kind": "snapshots",
            "page": 1,
            "page_size": 10,
        }
        self.assertEqual(HelperRequest.parse(payload).operation, "backup_list")
        for changes in ({"page": True}, {"page_size": 1000}, {"destination": "/etc"}):
            with self.assertRaises(ServiceError):
                validate_backup({**payload, **changes})
        for removed in ("backup_submit", "backup_outcome"):
            with self.assertRaises(ServiceError):
                validate_backup({**payload, "operation": removed})
