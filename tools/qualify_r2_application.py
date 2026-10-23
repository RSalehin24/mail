"""Run actual synthetic mail/dashboard recovery through R2; never a host-boot qualification."""

import argparse
import json
import os
import sys
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.restic_process import ResticProcess  # noqa: E402
from host.restic_repository import ResticRepository  # noqa: E402
from shared.files import atomic_json  # noqa: E402
from tests.maddy.test_restic_restore import NativeResticRestoreTests  # noqa: E402
from tools.qualify_restic import profile  # noqa: E402
from tools.run_tests import RecordingResult, source_digest, timestamp  # noqa: E402


class R2ApplicationRecovery(NativeResticRestoreTests):
    def create_repository(self, directory, staging, clock):
        configured = profile(self.report_directory, (
            self.credentials, Path("/usr/local/bin/restic"), self.installation,
        ))
        configured["backup_restic"]["staging_directory"] = str(staging)
        peer = ResticRepository(ResticProcess(configured), clock)
        identifier = peer.initialize()
        configured["backup_restic"]["repository_id"] = identifier
        atomic_json(self.report_directory / "profile.json", configured)
        return ResticRepository(ResticProcess(configured), clock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("credentials", type=Path)
    arguments = parser.parse_args()
    os.umask(0o077)
    identity, started = source_digest(), timestamp()
    identifier = str(uuid.uuid4())
    directory = ROOT / "build" / ("r2-application-" + identifier)
    directory.mkdir(mode=0o700)
    case = R2ApplicationRecovery(
        "test_actual_mail_and_enrolled_dashboard_restore_from_a_deduplicated_repository"
    )
    case.credentials, case.report_directory = arguments.credentials, directory
    case.installation = identifier
    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    before = time.monotonic()
    result = runner.run(unittest.TestSuite([case]))
    passed = (result.wasSuccessful() and len(result.records) == 1
              and all(value == "pass" for value in result.records.values())
              and source_digest() == identity)
    report = {
        "source_sha256": identity, "started_at": started, "completed_at": timestamp(),
        "elapsed_seconds": time.monotonic() - before,
        "result": "pass" if passed else "fail", "tests": result.records,
        "runtime_image": os.environ["MAIL_ADMIN_RUNTIME_IMAGE"],
        "qualification_image": os.environ["MAIL_ADMIN_QUALIFICATION_IMAGE"],
        "engine_architecture": os.environ["MAIL_ADMIN_ENGINE_ARCHITECTURE"],
        "scope": "Real R2, synthetic Maddy mail and enrolled dashboard; no full host boot",
    }
    atomic_json(directory / "result.json", report)
    print(json.dumps({"report": str(directory / "result.json"), "result": report["result"]}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
