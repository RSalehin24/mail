import os
import unittest
import uuid

from host.locks import LockPolicy
from host.retention import BackupRetention
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.support import StateFixture


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.index = self.fixture.root / "retention.json"
        atomic_json(self.index, {})
        self.index.with_suffix(".lock").touch(mode=0o600)
        self.retention = BackupRetention(
            self.fixture.root, (self.index, LockPolicy(1, os.getuid()), 60)
        )

    def record(self):
        identifier = str(uuid.uuid4())
        (self.fixture.root / identifier).mkdir()
        self.retention.record(
            {
                "snapshot_id": identifier,
                "verified": True,
                "reference": "synthetic-peer/" + identifier,
                "availability_observed_at": self.fixture.now,
            }
        )
        return identifier

    def test_old_verified_staging_is_pruned_but_current_and_unknown_entries_survive(self):
        old, current = self.record(), self.record()
        unknown = self.fixture.root / str(uuid.uuid4())
        unknown.mkdir()
        self.retention.prune((self.fixture.now + 61, {current}))
        self.assertFalse((self.fixture.root / old).exists())
        self.assertTrue((self.fixture.root / current).is_dir())
        self.assertTrue(unknown.is_dir())

    def test_unverified_or_path_like_retention_entry_cannot_authorize_cleanup(self):
        for identifier, verified in (("../escape", True), (str(uuid.uuid4()), False)):
            with self.assertRaises(ServiceError):
                self.retention.record({"snapshot_id": identifier, "verified": verified})
        self.assertEqual(protected_json(self.index, os.getuid()), {})

    def test_compressed_and_legacy_archives_are_pruned_with_their_recorded_snapshot(self):
        original = {path.name for path in self.fixture.root.iterdir()}
        identifier = self.record()
        for suffix in (".tar", ".tar.gz", ".encrypted", ".verified"):
            (self.fixture.root / (identifier + suffix)).write_bytes(b"synthetic archive")
        (self.fixture.root / (identifier + "-cipher")).mkdir()
        self.retention.prune((self.fixture.now + 61, set()))
        self.assertEqual(
            {path.name for path in self.fixture.root.iterdir()},
            original,
        )
