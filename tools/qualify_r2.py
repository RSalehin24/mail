"""Upload and restore a tiny encrypted synthetic archive; never read production mail."""

import argparse
import hashlib
import json
import os
import secrets
import sys
import tarfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.archive import decrypt_archive, encrypt_archive, extract_archive  # noqa: E402
from host.r2 import R2Options, r2_transfer  # noqa: E402
from shared.errors import ServiceError  # noqa: E402
from shared.files import atomic_json, protected_json  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402


def configuration(credentials: Path, identifier: str) -> dict:
    record = protected_json(credentials, os.getuid())
    bucket = record.get("bucket") or record.get("name")
    endpoint = record.get("endpoint") or record.get("s3_clients_endpoint") or record.get("s3_api")
    if not isinstance(bucket, str) or not isinstance(endpoint, str):
        raise ServiceError("r2_bucket_metadata_required", 400)
    parsed = urlsplit(endpoint.strip())
    if parsed.path.strip("/") not in {"", bucket}:
        raise ServiceError("r2_bucket_metadata_mismatch", 400)
    result = {
        "inputs": {"backup_destination": f"s3://{bucket}/mail-admin-fixtures/{identifier}/"},
        "backup_r2": {
            "endpoint": parsed.scheme + "://" + parsed.netloc,
            "credentials_path": str(credentials),
            "max_archive_bytes": 1024 * 1024,
            "max_bucket_bytes": 16 * 1024 * 1024,
            "retention_seconds": 7 * 86400,
        },
    }
    R2Options.load(result)
    return result


def fixture_archive(directory: Path, identifier: str) -> tuple[Path, Path, Path]:
    message = directory / "synthetic-message.eml"
    message.write_bytes(b"Subject: synthetic R2 backup qualification\n\nFixture only.\n" * 128)
    raw, sealed = directory / "snapshot.tar.gz", directory / (identifier + ".encrypted")
    with tarfile.open(raw, "x:gz") as archive:
        archive.add(message, "snapshot/synthetic-message.eml")
    key = directory / "fixture-decryption.key"
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as writer:
        writer.write(secrets.token_bytes(32))
    encrypt_archive(raw, sealed, key.read_bytes())
    return sealed, key, message


def qualify(directory: Path, profile: dict) -> dict:
    sealed, key, message = fixture_archive(directory, directory.name.removeprefix("r2-probe-"))
    transfer = r2_transfer(profile, os.getuid())
    reference = transfer.publish(sealed)
    atomic_json(directory / "published.json", {"reference": reference})
    downloaded = directory / "downloaded.encrypted"
    transfer.fetch(reference, downloaded)
    digest = hashlib.sha256(sealed.read_bytes()).hexdigest()
    if digest != hashlib.sha256(downloaded.read_bytes()).hexdigest():
        raise ServiceError("r2_fixture_integrity_failed", 503)
    raw, restored = directory / "downloaded.tar.gz", directory / "restored"
    decrypt_archive(downloaded, raw, key.read_bytes())
    extract_archive(raw, restored)
    if (restored / "snapshot/synthetic-message.eml").read_bytes() != message.read_bytes():
        raise ServiceError("r2_fixture_restore_failed", 503)
    return {
        "encrypted_bytes": sealed.stat().st_size,
        "archive_sha256": digest,
        "upload_download_integrity": "pass", "authenticated_synthetic_restore": "pass",
        "retained_synthetic_objects": 1,
        "scope": (
            "R2 synthetic archive transport; application and server restore remain unqualified"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("credentials", type=Path)
    arguments = parser.parse_args()
    os.umask(0o077)
    identifier = str(uuid.uuid4())
    directory = ROOT / "build" / ("r2-probe-" + identifier)
    directory.mkdir(mode=0o700)
    record = {
        "source_sha256": source_digest(),
        "started_at": datetime.now(UTC).isoformat(), "result": "failed",
    }
    started = time.monotonic()
    try:
        profile = configuration(arguments.credentials.resolve(), identifier)
        atomic_json(directory / "profile.json", profile)
        record.update(qualify(directory, profile))
        record["result"] = "pass"
    except ServiceError as error:
        record["error_code"] = error.code
    except OSError as error:
        record.update({"error_code": "r2_fixture_io_failed", "errno": error.errno})
    except (ValueError, TypeError):
        record["error_code"] = "invalid_r2_fixture_input"
    record.update({"finished_at": datetime.now(UTC).isoformat(),
                   "duration_seconds": round(time.monotonic() - started, 3)})
    atomic_json(directory / "result.json", record)
    print(json.dumps({"report": str(directory / "result.json"), "result": record["result"]}))
    return 0 if record["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
