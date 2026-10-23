import os
import select
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from host.legacy import LegacyRenewal
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.support import StateFixture


class LegacyProcessCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.record = self.fixture.root / "legacy.json"
        atomic_json(self.record, {"state": "entering"})
        docker = Mock()
        docker.run.return_value = ""
        self.guard = LegacyRenewal(self.record, 1, docker)

    def test_real_disappeared_manual_process_cannot_borrow_an_old_service_success(self):
        process = subprocess.Popen(
            ("/bin/sh", "-c", "printf 'ready\\n'; read fixture_completion",
             "/opt/maddy/renew-cert.sh"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self.close_process, process)
        self.assertTrue(select.select([process.stdout], [], [], 5)[0])
        self.assertEqual(process.stdout.read(6), b"ready\n")
        self.assertIsNone(process.poll())
        with self.assertRaisesRegex(ServiceError, "legacy_manual_run_active"):
            self.guard._check_process(Path("/proc") / str(process.pid))
        self.assertEqual(protected_json(self.record, os.getuid())["manual_completion"], "unknown")
        process.communicate(b"complete\n", timeout=5)
        self.assertEqual(process.returncode, 0)
        service = {"ActiveState": "inactive", "UnitFileState": "disabled",
                   "Result": "success", "ExecMainStatus": "0"}
        with patch.object(self.guard, "_show", return_value=service):
            with self.assertRaisesRegex(ServiceError, "legacy_manual_completion_unknown"):
                self.guard.verify()
        self.assertEqual(protected_json(self.record)["manual_writer_pid"], process.pid)

    @staticmethod
    def close_process(process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
