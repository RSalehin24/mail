"""Serve synthetic UI fixtures on loopback only; never a production entry point."""

import dataclasses
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


def main() -> None:
    fixture = ApiFixture()
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
                    "frontend_image_id": json.loads(
                        (ROOT / "build/frontend-export.json").read_text()
                    )["image_id"],
                },
                writer,
            )
        uvicorn.run(create_app(fixture.services), host="127.0.0.1", port=8765, access_log=False)
    finally:
        fixture.close()


if __name__ == "__main__":
    main()
