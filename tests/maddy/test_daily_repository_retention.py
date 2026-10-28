"""Exercise native forget/prune, deduplication and restoration after daily replacement."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from host.daily_retention import RetentionLedger
from shared.files import atomic_json, protected_json
from tests.restic_support import local_repository


class NativeDailyRetentionTests(unittest.TestCase):
    def test_verified_replacement_pins_and_interrupted_cleanup(self):
        with tempfile.TemporaryDirectory(dir=os.environ["MAIL_ADMIN_FIXTURE_ROOT"]) as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir(mode=0o700)
            now = 1793062800.0
            repository = local_repository(root, staging, lambda: now)
            ledger = RetentionLedger(repository)
            ids = []
            for index in range(3):
                source = staging / str(uuid.uuid4())
                source.mkdir(mode=0o700)
                atomic_json(source / "snapshot.json", {"cutoff": now - 300 + index * 60})
                (source / "messages").write_text("shared mail data" * 100)
                publication = repository.publish(source)
                repository.verify(publication)
                ids.append(publication["repository_snapshot"])
                if index != 2:
                    ledger.verified(ids[-1])
            # Unverified candidate cannot replace the previous verified snapshot.
            ledger.reconcile({ids[0]})
            self.assertEqual({row.identifier for row in repository.snapshots()}, set(ids))
            ledger.verified(ids[2])
            # A pending decision survives an interrupted native prune.
            with patch.object(repository, "prune", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    ledger.reconcile({ids[0]})
            self.assertEqual(protected_json(ledger.path)["cleanup"]["state"], "failed")
            RetentionLedger(repository).reconcile({ids[0]})
            self.assertEqual({row.identifier for row in repository.snapshots()}, {ids[0], ids[2]})
            RetentionLedger(repository).reconcile(set())
            self.assertEqual([row.identifier for row in repository.snapshots()], [ids[2]])
            repository.restore(repository.reference(ids[2]), root / "restored")
            self.assertEqual((root / "restored/messages").read_text(), "shared mail data" * 100)
