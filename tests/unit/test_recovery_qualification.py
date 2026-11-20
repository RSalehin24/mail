import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helper.recovery import QUALIFIED_FILES, require_qualified
from shared.errors import ServiceError
from tools import record_recovery_qualification as tool

ROOT = Path(__file__).resolve().parents[2]
PROOF_NAME = "recovery-qualified.json"


class RecoveryQualificationToolTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def install(self, proof):
        path = self.directory / PROOF_NAME
        path.write_text(json.dumps(proof))
        path.chmod(0o600)

    def test_the_proof_binds_exactly_the_qualified_files_with_the_documented_scope(self):
        proof = tool.build_proof(ROOT)
        self.assertEqual(set(proof["files"]), set(QUALIFIED_FILES))
        self.assertEqual((proof["schema_version"], proof["qualified"]), (1, True))
        self.assertEqual(proof["scope"], tool.SCOPE)

    def test_a_fresh_proof_is_accepted_by_the_recovery_gate(self):
        self.install(tool.build_proof(ROOT))
        require_qualified(self.directory, os.getuid())

    def test_one_changed_file_hash_makes_the_gate_refuse_recovery(self):
        proof = tool.build_proof(ROOT)
        proof["files"]["helper/recovery.py"] = "0" * 64
        self.install(proof)
        with self.assertRaises(ServiceError) as caught:
            require_qualified(self.directory, os.getuid())
        self.assertEqual(caught.exception.code, "recovery_qualification_changed")

    def test_no_proof_is_written_when_any_required_module_fails(self):
        output = self.directory / "new-proof.json"
        with (patch.object(tool, "failed_modules", return_value=["test_recovery"]),
              patch("sys.argv", ["record", str(output)])):
            self.assertEqual(tool.main(), 1)
        self.assertFalse(output.exists())

    def test_modules_run_in_the_real_maddy_suite_and_failures_are_reported(self):
        codes = iter([0, 1, 0])
        with patch.object(tool.subprocess, "run") as run:
            run.side_effect = lambda *args, **kwargs: SimpleNamespace(returncode=next(codes))
            failed = tool.failed_modules(("test_a", "test_b", "test_c"))
        self.assertEqual(failed, ["test_b"])
        self.assertIn("--module", run.call_args.args[0])
        self.assertIn("maddy", run.call_args.args[0])

    def test_the_changed_files_are_listed_against_the_previous_proof(self):
        previous = self.directory / "previous.json"
        previous.write_text(json.dumps({"files": {"a": "1", "b": "2", "gone": "3"}}))
        proof = {"files": {"a": "1", "b": "changed", "new": "4"}}
        self.assertEqual(tool.changed_files(proof, previous), ["b", "gone", "new"])

    def test_every_required_module_exists(self):
        for module in tool.REQUIRED_MODULES:
            self.assertTrue((ROOT / "tests/maddy" / (module + ".py")).is_file(), module)
