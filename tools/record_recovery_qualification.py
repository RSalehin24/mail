"""Re-issue the recovery qualification proof, only after the recovery test modules pass.

The proof (`recovery-qualified.json` in the helper state directory) pins the SHA-256 of the
recovery code files; `helper.recovery.require_qualified` refuses recovery if any file changed.
After an intended code change, this tool runs the real-Maddy recovery, restore, backup and
deletion test modules in Docker and, only if every one passes, writes a new proof for the
files as they are now. The owner reviews the printed changes before installing the file.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from helper.recovery import QUALIFIED_FILES  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402

SCOPE = (
    "Qualified recoverable deletion and additive all-mailbox backup restore; "
    "permanent deletion remains disabled"
)
SCHEMA_VERSION = 1
# Real-Maddy modules that exercise recoverable deletion, additive restore, backup and the
# deletion guard; each runs in its own isolated controller via tools/run_linux.py.
REQUIRED_MODULES = (
    "test_mailbox_recovery_store",
    "test_mailbox_recovery_worker",
    "test_recovery",
    "test_recovery_https",
    "test_runtime_pipe_recovery",
    "test_restic_restore",
    "test_restic_reconciliation",
    "test_backup_merge",
    "test_daily_repository_retention",
    "test_deletion_guard",
    "test_guarded_deletion",
    "test_mutation_crashes",
    "test_journal_storage",
)


def build_proof(root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "qualified": True,
        "scope": SCOPE,
        "source_sha256": source_digest(),
        "files": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted(QUALIFIED_FILES)
        },
    }


def failed_modules(modules: tuple[str, ...]) -> list[str]:
    failures = []
    for module in modules:
        command = (sys.executable, str(ROOT / "tools/run_linux.py"), "--suite", "maddy",
                   "--module", module)
        if subprocess.run(command, cwd=ROOT, check=False).returncode:  # noqa: S603 -- fixed argv.
            failures.append(module)
    return failures


def changed_files(proof: dict[str, Any], previous: Path) -> list[str]:
    old = json.loads(previous.read_text()).get("files", {})
    new = proof["files"]
    return sorted(name for name in new.keys() | old.keys() if old.get(name) != new.get(name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="where to write the new proof")
    parser.add_argument("--previous", type=Path, help="the proof being replaced, to list changes")
    arguments = parser.parse_args()
    failures = failed_modules(REQUIRED_MODULES)
    if failures:
        print("No proof written; failing modules: " + ", ".join(failures), file=sys.stderr)
        return 1
    proof = build_proof(ROOT)
    arguments.output.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {arguments.output}: {len(proof['files'])} files bound")
    if arguments.previous:
        print("Files changed since the previous proof:")
        print("\n".join("  " + name for name in changed_files(proof, arguments.previous)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
