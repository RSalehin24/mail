import json
import tempfile
import unittest
import uuid
from pathlib import Path

from helper.backup_control import BackupControl, validate_backup
from helper.protocol import HelperRequest
from shared.errors import ServiceError


class BackupControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.control = BackupControl(Path(self.directory.name))

    def values(self, **changes):
        return {
            "kind": "backup",
            "snapshot": "",
            "previous": "",
            "confirmation": "",
            "acknowledged": True,
            "password_hashes": {},
            **changes,
        }

    def submit(self, values, identifier=None):
        return self.control.handle("backup_submit", identifier or str(uuid.uuid4()), values, 1000)

    def test_durable_idempotence_and_failure_retry(self):
        identifier = str(uuid.uuid4())
        self.assertEqual(self.submit(self.values(), identifier)["state"], "queued")
        self.assertEqual(self.submit(self.values(), identifier)["state"], "queued")
        with self.assertRaisesRegex(ServiceError, "request_id_conflict"):
            self.submit(self.values(kind="refresh"), identifier)
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.submit(self.values())
        self.control.update(identifier, "failed", "capture", 1001, "disk_full")
        restarted = BackupControl(Path(self.directory.name))
        self.assertEqual(restarted.job(identifier)["error"], "disk_full")
        retry = self.submit(self.values(previous=identifier))
        self.assertNotEqual(retry["id"], identifier)

    def test_catalog_numbered_pages_clamp_and_preserve_observation(self):
        with self.control.connect() as connection:
            connection.executemany(
                "INSERT INTO snapshots VALUES(?,?,?)", [(f"{i:064x}", i, 100) for i in range(125)]
            )
            connection.execute("INSERT INTO observations VALUES(1,100,?)", ("",))
        result = self.control.handle(
            "backup_list",
            str(uuid.uuid4()),
            {"kind": "snapshots", "page": 99, "page_size": 10},
            9999,
        )
        self.assertEqual((result["page"], result["total"], len(result["items"])), (13, 125, 5))
        self.assertEqual(result["observation"]["observed"], 100)

    def test_closed_protocol_refuses_paths_invalid_pages_and_passwords(self):
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
        with self.assertRaises(ServiceError):
            validate_backup(
                {
                    "protocol": 1,
                    "operation": "backup_submit",
                    "request_id": str(uuid.uuid4()),
                    **self.values(password_hashes={"cloud@rsalehin24.me": "plaintext"}),
                }
            )

    def test_restore_requires_matching_verified_preflight_and_exact_missing_passwords(self):
        snapshot = "a" * 64
        with self.control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (snapshot, 1, 1))
        prepared = self.submit(
            self.values(kind="preflight", snapshot=snapshot, confirmation=snapshot)
        )
        self.control.update(
            prepared["id"],
            "complete",
            "complete",
            1002,
            result={"missing": ["missing@rsalehin24.me"]},
        )
        with self.assertRaisesRegex(ServiceError, "missing_mailbox_passwords_required"):
            self.submit(
                self.values(
                    kind="restore",
                    snapshot=snapshot,
                    confirmation=snapshot,
                    previous=prepared["id"],
                )
            )
        self.assertNotIn("payload", self.control.job(prepared["id"]))
        with self.control.connect() as connection:
            encoded = connection.execute(
                "SELECT payload FROM jobs WHERE id=?", (prepared["id"],)
            ).fetchone()[0]
        self.assertIsInstance(json.loads(encoded), dict)

    def test_uncertain_operation_requires_reconciliation_before_retry(self):
        original = self.submit(self.values())
        self.control.update(original["id"], "reconciliation_required", "backup_capture", 1001)
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.submit(self.values(previous=original["id"]))
        result = self.submit(self.values(kind="reconcile", previous=original["id"]))
        self.assertEqual(result["state"], "queued")
        with self.assertRaisesRegex(ServiceError, "backup_operation_in_progress"):
            self.submit(self.values(kind="reconcile", previous=original["id"]))


class BackupPublicationOutcomeTests(unittest.TestCase):
    def test_catalog_failure_does_not_hide_verified_publication(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        from host.backup_control import BackupWorker

        worker = BackupWorker.__new__(BackupWorker)
        worker.context = SimpleNamespace(backup=lambda: object(), deployment={})
        worker.phase = Mock()
        worker.catalog = Mock(
            side_effect=ServiceError("backup_command_reconciliation_required", 409)
        )
        with patch("host.backup_control.BackupOperations") as operations:
            operations.return_value.run.return_value = {"reference": "verified-reference"}
            result = worker.perform({"kind": "backup"})
        self.assertTrue(result["verified"])
        self.assertEqual(result["reference"], "verified-reference")
        self.assertEqual(result["cleanup"]["state"], "failed")
