"""Serve synthetic UI fixtures on loopback only; never a production entry point."""

import dataclasses
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.main import create_app  # noqa: E402
from tests.api_support import ApiFixture  # noqa: E402
from tests.auth_support import PASSWORD  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402


def frontend_identity() -> str:
    path = ROOT / "build/frontend-export.json"
    if path.is_file():
        record = json.loads(path.read_text())
        observed = {
            str(asset.relative_to(ROOT / "frontend/dist")):
                hashlib.sha256(asset.read_bytes()).hexdigest()
            for asset in (ROOT / "frontend/dist").rglob("*") if asset.is_file()
        }
        if observed == record.get("files"):
            return str(record["image_id"])
    return "local-vite-build-not-image-qualified"


def main() -> None:
    fixture = ApiFixture()
    from tests.recovery_support import configure_recovery
    recovery = configure_recovery(fixture)
    from shared.files import atomic_json
    policy = recovery.policy()
    policy['protected'].update({f'protected{i:02d}@rsalehin24.me': 'Synthetic pagination fixture'
                                for i in range(14)})
    atomic_json(recovery.policy_path, policy)
    backups = fixture.host.helper.dependencies.backups
    with backups.connect() as connection:
        connection.executemany('INSERT INTO snapshots VALUES(?,?,?)',
                               [(f'{i + 1:064x}', 1700000000 + i, 1700000200)
                                for i in range(125)])
        connection.execute('INSERT INTO observations VALUES(1,1700000200,?)', ('',))
    import uuid
    failed = str(uuid.uuid4())
    values = {'kind': 'backup', 'snapshot': '', 'previous': '', 'confirmation': '',
              'acknowledged': True, 'password_hashes': {}}
    backups.handle('backup_submit', failed, values, 1700000000)
    backups.update(failed, 'failed', 'capture', 1700000001, 'synthetic_transfer_failure')
    from tests.telemetry_support import synthetic_resources
    fixture.host.helper.dependencies = dataclasses.replace(
        fixture.host.helper.dependencies, status=synthetic_resources,
    )
    try:
        shutil.copytree(
            ROOT / "frontend/dist", fixture.services.settings.static_directory, dirs_exist_ok=True
        )
        fixture.services.settings = dataclasses.replace(
            fixture.services.settings, origin="http://localhost:8765"
        )
        names = (
            "cloud",
            "demo_user.library",
            "dev_01",
            "dev_02",
            "library",
            "mail",
            "reyan",
            "salehin",
        )
        for name in names:
            email = name + "@rsalehin24.me"
            fixture.host.maddy.credentials.add(email)
            fixture.host.maddy.storage.add(email)
        report = ROOT / "build/ui-fixture.json"
        descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as writer:
            json.dump(
                {
                    "scope": "synthetic local fixture only",
                    "password": PASSWORD,
                    "totp": fixture.code(),
                    "recovery_code": fixture.codes[-1],
                    "source_sha256": source_digest(),
                    "frontend_image_id": frontend_identity(),
                },
                writer,
            )
        uvicorn.run(create_app(fixture.services), host="127.0.0.1", port=8765, access_log=False)
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
