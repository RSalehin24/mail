"""Qualify real R2 sharing and seven-day retention with synthetic files, never live mail."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from host.restic_process import ResticProcess  # noqa: E402
from host.restic_repository import ResticRepository  # noqa: E402
from shared.errors import ServiceError  # noqa: E402
from shared.files import atomic_json  # noqa: E402
from tools.fetch_restic import VERSION  # noqa: E402
from tools.qualify_r2 import configuration  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402


def profile(directory: Path, inputs: tuple[Path, Path, str]) -> dict:
    credentials, executable, installation = inputs
    result = configuration(credentials, "restic-" + installation)
    result["backup_r2"].update({"max_archive_bytes": 16 * 1024 * 1024,
                                "max_bucket_bytes": 64 * 1024 * 1024})
    state, staging = directory / "jobs", directory / "staging"
    for path in (state, staging):
        path.mkdir(mode=0o700)
    (state / "command.lock").touch(mode=0o600)
    password = directory / "recovery-password.key"
    password.write_text(secrets.token_urlsafe(48) + "\n")
    password.chmod(0o600)
    result["backup_restic"] = {
        "executable": str(executable), "executable_sha256": hashlib.sha256(
            executable.read_bytes()
        ).hexdigest(), "password_file": str(password), "state_directory": str(state),
        "staging_directory": str(staging), "repository_id": None,
        "installation_id": installation, "command_timeout_seconds": 120,
    }
    return result


def repository(configured: dict, now: float) -> ResticRepository:
    return ResticRepository(ResticProcess(configured, os.getuid()), lambda: now)


def make_snapshot(source: Path, target: Path, cutoff: float) -> Path:
    target = target / str(uuid.uuid4())
    target.mkdir(mode=0o700)
    shutil.copytree(source, target / "maddy")
    atomic_json(target / "snapshot.json", {"cutoff": cutoff, "accounts": {}, "inventory": {}})
    return target


def snapshots(peer: ResticRepository, directory: Path, now: float) -> list[dict]:
    source = directory / "synthetic-source"
    source.mkdir(mode=0o700)
    payload = source / "shared-content.bin"
    payload.write_bytes(secrets.token_bytes(8 * 1024 * 1024))
    payload.chmod(0o600)
    (source / "old-message.eml").write_text("Subject: old fixture\n\nSynthetic old message.\n")
    published = []
    for day in range(10):
        if day >= 2:
            (source / "day-message.eml").write_text("Synthetic version " + str(day))
        if day == 2:
            (source / "old-message.eml").unlink()
        snapshot = make_snapshot(source, peer.staging, now - (9 - day) * 86400)
        publication = peer.publish(snapshot)
        peer.verify(publication)
        published.append(publication)
        atomic_json(directory / "published.json", {"snapshots": published})
        print(json.dumps({"synthetic_day": day, "data_added_bytes":
                          publication["data_added_bytes"], "verified": True}), flush=True)
    if published[1]["data_added_bytes"] >= payload.stat().st_size // 10:
        raise ServiceError("unchanged_data_was_not_shared", 503)
    return published


def qualify(configured: dict, directory: Path) -> dict:
    now = float(int(time.time()))
    peer = repository(configured, now)
    identifier = peer.initialize()
    configured["backup_restic"]["repository_id"] = identifier
    atomic_json(directory / "profile.json", configured)
    peer = repository(configured, now)
    published = snapshots(peer, directory, now)
    expired = peer.prune({published[-1]["repository_snapshot"]})
    retained = peer.snapshots()
    if len(expired) != 3 or len(retained) != 7:
        raise ServiceError("seven_day_retention_failed", 503)
    for point in retained:
        target = directory / ("replacement-" + point.identifier)
        peer.restore(peer.reference(point.identifier), target)
        expected = directory / "synthetic-source/shared-content.bin"
        if (target / "maddy/shared-content.bin").read_bytes() != expected.read_bytes():
            raise ServiceError("shared_content_lost_after_prune", 503)
    try:
        peer.restore(published[0]["reference"], directory / "expired-point")
    except ServiceError as error:
        if error.code != "restic_snapshot_unavailable":
            raise
    else:
        raise ServiceError("expired_point_still_available", 503)
    return {
        "repository_id": identifier, "restic_version": VERSION,
        "snapshot_count_before": len(published), "retained_points": len(retained),
        "expired_points": len(expired), "all_retained_points_restored": True,
        "first_upload_data_bytes": published[0]["data_added_bytes"],
        "unchanged_second_upload_data_bytes": published[1]["data_added_bytes"],
        "test_clock_utc": datetime.fromtimestamp(now, UTC).isoformat(),
        "scope": (
            "Real R2 and synthetic files; not full application or replacement-host qualification"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("credentials", type=Path)
    parser.add_argument("executable", type=Path)
    arguments = parser.parse_args()
    os.umask(0o077)
    identifier = str(uuid.uuid4())
    directory = ROOT / "build" / ("restic-probe-" + identifier)
    directory.mkdir(mode=0o700)
    configured = profile(directory, (arguments.credentials.resolve(),
                                     arguments.executable.resolve(), identifier))
    atomic_json(directory / "profile.json", configured)
    report = {"source_sha256": source_digest(), "result": "failed",
              "started_at": datetime.now(UTC).isoformat()}
    try:
        report.update(qualify(configured, directory))
        report["result"] = "pass"
    except ServiceError as error:
        report["error_code"] = error.code
    except OSError as error:
        report.update({"error_code": "restic_fixture_io_failed", "errno": error.errno})
    report["finished_at"] = datetime.now(UTC).isoformat()
    atomic_json(directory / "result.json", report)
    print(json.dumps({"report": str(directory / "result.json"), "result": report["result"]}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
