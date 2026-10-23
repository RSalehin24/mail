import hashlib
import os
import unittest
from datetime import UTC, datetime

from host.rollout import RolloutGuard, RolloutPaths
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.support import StateFixture


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.paths = RolloutPaths(self.fixture.root, self.fixture.root / "approval.json")
        (self.fixture.root / "reports").mkdir()
        self.deployment = {
            "artifact_set_sha256": "a" * 64,
            "specification_sha256": "b" * 64,
            "qualification_suite_sha256": "c" * 64,
            "inputs": {
                "target_instance_id": "synthetic/project/zone/instance",
                "target_ssh_host_key_sha256": "d" * 64,
            },
        }
        self.guard = RolloutGuard(
            self.deployment, (self.paths, os.getuid(), lambda: self.fixture.now)
        )

    def timestamp(self, offset):
        return datetime.fromtimestamp(self.fixture.now + offset, UTC).isoformat()

    def approval(self):
        return {
            "artifact_set_sha256": "a" * 64,
            "target": self.deployment["inputs"],
            "approved_by": "synthetic test operator",
            "allowed_phases": ["maintenance"],
            "approved_at_utc": self.timestamp(-10),
            "expires_at_utc": self.timestamp(100),
        }

    def test_explicit_approval_is_artifact_target_phase_and_time_bound(self):
        record = self.approval()
        atomic_json(self.paths.approval, record)
        self.guard.approval("maintenance")
        for key, value in (
            ("artifact_set_sha256", None),
            ("target", {}),
            ("allowed_phases", ["recovery"]),
            ("expires_at_utc", self.timestamp(-1)),
        ):
            atomic_json(self.paths.approval, {**record, key: value})
            with self.assertRaises(ServiceError):
                self.guard.approval("maintenance")

    def test_missing_deployment_identities_never_match_null_approval(self):
        record = self.approval()
        record["artifact_set_sha256"] = None
        self.deployment["artifact_set_sha256"] = None
        atomic_json(self.paths.approval, record)
        with self.assertRaises(ServiceError):
            self.guard.approval("maintenance")

    def test_gate_requires_matching_immutable_report_and_exact_cases(self):
        report = {
            "schema_version": 1,
            "test_id": "G01",
            "artifact_set_sha256": "a" * 64,
            "specification_sha256": "b" * 64,
            "runner_revision": "c" * 64,
            "environment": "isolated-linux-amd64",
            "started_at_utc": self.timestamp(-20),
            "completed_at_utc": self.timestamp(-10),
            "result": "pass",
            "cases": [{"id": "G01.01", "result": "pass"}],
        }
        path = self.fixture.root / "reports/G01.json"
        atomic_json(path, report)
        reference = {
            "report": "reports/G01.json",
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        atomic_json(self.fixture.root / "checkpoints.json", {"G01": reference})
        self.guard.checkpoint("G01")
        atomic_json(path, {**report, "cases": []})
        with self.assertRaises(ServiceError):
            self.guard.checkpoint("G01")
        reference["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(self.fixture.root / "checkpoints.json", {"G01": reference})
        with self.assertRaises(ServiceError):
            self.guard.checkpoint("G01")
