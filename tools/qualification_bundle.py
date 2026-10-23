"""Bind executing qualification code to all four immutable release artifacts."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs/mail-admin-implementation/tools"))

from check_release import (  # noqa: E402
    load_context,
    validate_artifacts,
    validate_identity,
    verified_file,
)
from verification_support import read_json  # noqa: E402

from tools.qualification_archive import ArtifactArchive, image_identity  # noqa: E402
from tools.run_tests import source_identities  # noqa: E402


class QualificationBundle:
    def __init__(self, evidence: Path) -> None:
        self.context = load_context(evidence, None)
        validate_identity(self.context)
        validate_artifacts(self.context)
        self.artifacts = {
            entry["id"]: verified_file(self.context, entry)
            for entry in self.context["evidence"]["artifacts"]
        }
        self.image = image_identity(self.artifacts["dashboard_image"])
        self.verify_host_bundle()

    @property
    def identity(self) -> dict[str, str]:
        evidence = self.context["evidence"]
        return {key: evidence[key] for key in (
            "artifact_set_sha256", "specification_sha256", "runner_revision"
        )}

    def verify_artifacts(self) -> None:
        validate_identity(self.context)
        validate_artifacts(self.context)
        self.verify_host_bundle()

    def verify_host_bundle(self) -> None:
        configuration = read_json(self.artifacts["deployment_config"])
        with (ArtifactArchive(self.artifacts["qualification_suite"]) as suite,
              ArtifactArchive(self.artifacts["host_bundle"]) as host):
            expected = {name for name in suite.members if name.startswith(
                ("host/", "helper/", "shared/", "docs/mail-admin-implementation/")
            ) or name == "docker-compose.yml"}
            if set(host.members) != expected | {"bin/restic", "bin/restic.json"}:
                raise ValueError("Host bundle file set differs from the qualified suite")
            if any(host.digest(name) != suite.digest(name) for name in expected):
                raise ValueError("Host bundle content differs from the qualified suite")
            if host.digest("bin/restic") != configuration["backup_restic"]["executable_sha256"]:
                raise ValueError("Host Restic binary differs from qualified configuration")

    def verify_source(self, root: Path | None = None) -> None:
        identities = source_identities(root)
        with ArtifactArchive(self.artifacts["qualification_suite"]) as archive:
            for name, digest in identities.items():
                if name not in archive.members or archive.digest(name) != digest:
                    raise ValueError("Executing source differs from the frozen qualification suite")
            frozen = {name for name in archive.members if _source_member(name)}
        if frozen != identities.keys():
            raise ValueError("Qualification source file set differs from the frozen suite")


def _source_member(name: str) -> bool:
    return name.startswith(("backend/", "shared/", "helper/", "host/", "tests/", "tools/",
                            "frontend/src/")) or name in {
        "Dockerfile", "docker-compose.yml", "Makefile", "pyproject.toml",
        "requirements.lock", "requirements-dev.lock", "frontend/package.json",
        "frontend/package-lock.json", "frontend/tsconfig.json", "frontend/index.html",
    }
