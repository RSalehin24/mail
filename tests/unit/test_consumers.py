import os
import tempfile
import unittest
from pathlib import Path

from host.consumers import CONSUMERS, ConsumerIntents
from shared.errors import ServiceError

NOW = 1_800_000_000.0


class ConsumerIntentsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "consumers.json"
        self.intents = ConsumerIntents(self.path, os.getuid(), lambda: NOW)

    def test_a_missing_record_means_both_consumers_are_expected_running(self):
        record = self.intents.read()
        self.assertEqual(set(record["consumers"]), set(CONSUMERS))
        intents = [consumer["intent"] for consumer in record["consumers"].values()]
        self.assertEqual(set(intents), {"expected_running"})
        self.assertFalse(self.path.exists())

    def test_maintenance_survives_a_restart_with_its_reason_and_fingerprint(self):
        self.intents.maintenance("maddy", "backup_capture", "a" * 64)
        reloaded = ConsumerIntents(self.path, os.getuid()).read()["consumers"]
        self.assertEqual(reloaded["maddy"], {
            "intent": "maintenance", "reason": "backup_capture", "served_sha256": "a" * 64,
        })
        self.assertEqual(reloaded["mail-admin"]["intent"], "expected_running")
        self.assertEqual(self.intents.read()["observed_at"], NOW)

    def test_register_returns_a_consumer_to_expected_running(self):
        self.intents.maintenance("mail-admin", "offline_recovery")
        self.intents.register("mail-admin")
        self.assertEqual(self.intents.read()["consumers"]["mail-admin"],
                         {"intent": "expected_running"})

    def test_unknown_consumers_are_rejected(self):
        for method in (self.intents.register, lambda name: self.intents.maintenance(name, "x")):
            with self.assertRaises(ServiceError):
                method("postgres")
