"""Synthetic gate records for testing rollout rejection; never release evidence."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from shared.files import atomic_json


def write_gate_fixtures(directory, deployment, now):
    # Synthetic parser inputs inside the unit temp directory, never release evidence.
    contracts = Path(__file__).resolve().parents[1] / (
        "docs/mail-admin-implementation/requirements/contracts.json"
    )
    gates = json.loads(contracts.read_bytes())["gates"]
    (directory / "reports").mkdir(mode=0o700)
    references = {}
    for index, gate in enumerate(gates):
        stamp = now - 1000 + index * 2
        report = {
            "schema_version": 1, "test_id": gate["id"],
            "artifact_set_sha256": deployment["artifact_set_sha256"],
            "specification_sha256": deployment["specification_sha256"],
            "runner_revision": deployment["qualification_suite_sha256"],
            "environment": gate["environment"], "result": "pass",
            "started_at_utc": datetime.fromtimestamp(stamp, UTC).isoformat(),
            "completed_at_utc": datetime.fromtimestamp(stamp + 1, UTC).isoformat(),
            "cases": [{"id": case["id"], "result": "pass"} for case in gate["cases"]],
            "observed_target": {key: value for key, value in deployment["inputs"].items()
                                if key != "lock_wait_seconds"},
        }
        name = "reports/" + gate["id"] + ".json"
        atomic_json(directory / name, report)
        references[gate["id"]] = {
            "report": name,
            "report_sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
        }
    atomic_json(directory / "checkpoints.json", references)
