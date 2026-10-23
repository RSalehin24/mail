import os
import select
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.restic_support import local_repository


class ResticReconciliationTests(unittest.TestCase):
    def test_a_real_surviving_process_blocks_reconciliation_until_repository_verification(self):
        with tempfile.TemporaryDirectory(dir=os.environ["MAIL_ADMIN_FIXTURE_ROOT"]) as temporary:
            root = Path(temporary)
            staging = root / "staging"
            staging.mkdir(mode=0o700)
            repository = local_repository(root, staging, time.time)
            cache = repository.process.paths.state / (str(uuid.uuid4()) + "-cache")
            cache.mkdir(mode=0o700)
            path = cache.with_name(cache.name + ".json")
            atomic_json(path, {"state": "started", "cache": str(cache)})
            self.observe_survivor(repository, (cache, path))
            repository.process.reconcile()
            result = protected_json(path)
            self.assertEqual(result["state"], "reconciled")
            self.assertIsNone(result["exit_code"])
            self.assertEqual(result["outcome"], "exit_unknown_repository_checked")
            self.assertFalse(cache.exists())

    def observe_survivor(self, repository, paths):
        cache, journal = paths
        program = "import sys; print('ready',flush=True); sys.stdin.buffer.read()"
        with subprocess.Popen((sys.executable, "-c", program, str(cache)),
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE) as child:
            try:
                self.assertTrue(select.select([child.stdout], [], [], 5)[0])
                self.assertEqual(child.stdout.read(6), b"ready\n")
                self.assertIsNone(child.poll())
                self.assert_reconciliation_blocked(repository, journal)
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                child.wait(timeout=5)

    def assert_reconciliation_blocked(self, repository, path):
        with self.assertRaisesRegex(ServiceError, "backup_command_still_running"):
            repository.process.reconcile()
        self.assertEqual(protected_json(path)["state"], "started")
