"""Record individual unittest outcomes; these are local checks, not release evidence."""

import argparse
import gc
import hashlib
import json
import os
import platform
import re
import sys
import unittest
import uuid
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def controller_memory() -> dict[str, int | str | None]:
    root = Path("/sys/fs/cgroup")
    if not all((root / name).is_file() for name in ("memory.max", "memory.peak", "memory.current")):
        return {"state": "unavailable"}
    maximum = (root / "memory.max").read_text().strip()
    return {
        "state": "observed",
        "limit_bytes": None if maximum == "max" else int(maximum),
        "peak_bytes": int((root / "memory.peak").read_text()),
        "current_bytes": int((root / "memory.current").read_text()),
        "scope": "qualification controller including helper and child processes; not whole host",
    }


def source_identities(root: Path | None = None) -> dict[str, str]:
    root = root or ROOT
    files = {
        path
        for directory in ("backend", "shared", "helper", "host", "tests", "tools")
        for path in (root / directory).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    for directory in ("frontend/src", "frontend/public"):
        files.update(path for path in (root / directory).rglob("*") if path.is_file())
    files.update(
        root / name
        for name in (
            "Dockerfile",
            "docker-compose.yml",
            "Makefile",
            "pyproject.toml",
            "requirements.lock",
            "requirements-dev.lock",
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/tsconfig.json",
            "frontend/index.html",
            "tools/check_browser.mjs",
        )
    )
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(files)
    }


def source_digest() -> str:
    identities = source_identities()
    return hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()


def append_progress(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as writer:
        writer.write(json.dumps(record) + "\n")
        writer.flush()
        os.fsync(writer.fileno())


def validate_module(suite: str, module: str | None) -> Path:
    directory = ROOT / "tests" / suite
    if module is not None and (
        suite not in {"maddy", "runtime", "acme"}
        or re.fullmatch(r"test_[a-z0-9_]+", module) is None
        or not (directory / (module + ".py")).is_file()
    ):
        raise ValueError("An existing Maddy, runtime or ACME test module is required")
    return directory


def test_selection(suite: str, module: str | None) -> unittest.TestSuite:
    directory = validate_module(suite, module)
    pattern = module + ".py" if module is not None else "test*.py"
    return unittest.defaultTestLoader.discover(str(directory), pattern=pattern)


def report_directory(selected: Path | None, module: str | None) -> Path:
    if module is not None and selected is None:
        raise ValueError("An isolated module requires a distinct report directory")
    directory = (ROOT / selected).resolve() if selected is not None else ROOT / "build"
    if not directory.is_relative_to(ROOT / "build") or (
        module is not None and directory == ROOT / "build"
    ):
        raise ValueError("Reports must remain in a distinct build subdirectory")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *arguments: Any, **keywords: Any) -> None:
        self.progress: Path | None = keywords.pop("progress", None)
        super().__init__(*arguments, **keywords)
        self.records: dict[str, str] = {}

    def startTest(self, test: unittest.TestCase) -> None:
        # Completed fixtures can contain cycles; reclaim them before the next bounded job.
        gc.collect()
        super().startTest(test)
        self._observe(test, "started")

    def stopTest(self, test: unittest.TestCase) -> None:
        self._observe(test, self.records.get(test.id(), "unknown"))
        super().stopTest(test)

    def _observe(self, test: unittest.TestCase, outcome: str) -> None:
        if self.progress is None:
            return
        record = {"test": test.id(), "outcome": outcome, "observed_at_utc": timestamp(),
                  "controller_memory": controller_memory()}
        append_progress(self.progress, record)

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self.records[test.id()] = "pass"

    def addFailure(self, test: unittest.TestCase, error: Any) -> None:
        super().addFailure(test, error)
        self.records[test.id()] = "fail"

    def addError(self, test: unittest.TestCase, error: Any) -> None:
        super().addError(test, error)
        self.records[test.id()] = "error"

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self.records[test.id()] = "skipped"

    def addExpectedFailure(self, test: unittest.TestCase, error: Any) -> None:
        super().addExpectedFailure(test, error)
        self.records[test.id()] = "expected_failure"

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self.records[test.id()] = "unexpected_success"

    def addSubTest(self, test: unittest.TestCase, subtest: unittest.TestCase, error: Any) -> None:
        super().addSubTest(test, subtest, error)
        if error is not None:
            self.records[test.id()] = "fail"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", choices=("unit", "integration", "maddy", "runtime", "acme"))
    parser.add_argument("--module")
    parser.add_argument("--report-directory", type=Path)
    arguments = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    started = timestamp()
    identity = source_digest()
    directory = report_directory(arguments.report_directory, arguments.module)
    progress = directory / (f"{arguments.suite}-progress-" + str(uuid.uuid4()) + ".jsonl")
    progress.touch(mode=0o600, exist_ok=False)
    append_progress(progress, {
        "scope": "application test progress; incomplete until the final result exists",
        "suite": arguments.suite, "source_sha256": identity, "started_at_utc": started,
        "qualification_image": os.environ.get("MAIL_ADMIN_QUALIFICATION_IMAGE"),
        "runtime_image": os.environ.get("MAIL_ADMIN_RUNTIME_IMAGE"),
    })
    suite = test_selection(arguments.suite, arguments.module)
    runner = unittest.TextTestRunner(
        verbosity=2, resultclass=partial(RecordingResult, progress=progress)
    )
    result = runner.run(suite)
    passed = (
        result.wasSuccessful()
        and result.testsRun > 0
        and len(result.records) == result.testsRun
        and all(value == "pass" for value in result.records.values())
        and identity == source_digest()
    )
    report = {
        "schema_version": 1,
        "scope": "local application checks; not qualification",
        "suite": arguments.suite,
        "module": arguments.module,
        "source_sha256": identity,
        "started_at_utc": started,
        "completed_at_utc": timestamp(),
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "qualification_image": os.environ.get("MAIL_ADMIN_QUALIFICATION_IMAGE"),
        "runtime_image": os.environ.get("MAIL_ADMIN_RUNTIME_IMAGE"),
        "docker_engine_architecture": os.environ.get("MAIL_ADMIN_ENGINE_ARCHITECTURE"),
        "controller_memory": controller_memory(),
        "progress_record": progress.name,
        "result": "pass" if passed else "fail",
        "tests": result.records,
        "production_qualification": "blocked",
    }
    (ROOT / "build").mkdir(exist_ok=True)
    prefix = os.environ.get("MAIL_ADMIN_REPORT_PREFIX", "")
    if prefix not in {"", "linux-"}:
        raise ValueError("Invalid local report prefix")
    (directory / f"{prefix}{arguments.suite}-checks.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
