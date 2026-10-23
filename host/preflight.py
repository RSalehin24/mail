"""Read-only checks for bind identity, privileges and approved immutable image inputs."""

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from helper.process import Command, execute
from host.container_policy import TEMPORARY_PATH, ContainerPolicy
from host.docker import Docker
from host.rollout import operational_guard
from shared.errors import ServiceError
from shared.files import protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from shared.validation import HELPER_PROTOCOL, SCHEMA_VERSION


def qualify_preflight(deployment: dict[str, Any]) -> None:
    directory = Path(deployment["evidence_bundle_directory"])
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ServiceError("untrusted_evidence_store", 503)
    evidence = protected_json(directory / "evidence.json")
    if evidence.get("artifact_set_sha256") != deployment.get("artifact_set_sha256"):
        raise ServiceError("qualified_artifact_set_required", 409)
    _configuration_binding(deployment, (directory, evidence))
    ContainerPolicy.load(deployment).arguments((TEMPORARY_PATH, "/var/log/letsencrypt"))
    checker = (
        Path(__file__).resolve().parents[1]
        / "docs/mail-admin-implementation/tools/check_release.py"
    )
    result = execute(
        Command(
            (
                sys.executable,
                str(checker),
                str(directory / "evidence.json"),
                "--phase",
                "preflight",
            ),
            timeout=60,
        )
    )
    if result.code:
        raise ServiceError("preflight_qualification_blocked", 409)


def _configuration_binding(deployment: dict[str, Any], bundle: tuple[Path, dict[str, Any]]) -> None:
    directory, evidence = bundle
    effective = {
        key: value
        for key, value in deployment.items()
        if key
        not in {
            "artifact_set_sha256",
            "specification_sha256",
            "qualification_suite_sha256",
            "evidence_bundle_directory",
        }
    }
    artifact = next(
        (entry for entry in evidence["artifacts"] if entry["id"] == "deployment_config"), None
    )
    if artifact is None:
        raise ServiceError("qualified_configuration_required", 409)
    path = directory / artifact["path"]
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ServiceError("untrusted_configuration_path", 503)
    qualified = json.loads(path.read_bytes())
    if _canonical(effective) != _canonical(qualified):
        raise ServiceError("effective_configuration_drift", 409)


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def require_approval(deployment: dict[str, Any], phase: str) -> None:
    operational_guard(deployment).approval(phase)


def require_checkpoint(phase: str, deployment: dict[str, Any]) -> dict[str, Any]:
    return operational_guard(deployment).checkpoint(phase)


def validate_binds(deployment: dict[str, Any]) -> None:
    inputs = deployment["inputs"]
    sources = (
        (Path("/opt/mail-admin/data"), "directory", inputs["service_uid"]),
        (Path("/run/mail-admin-helper"), "directory", 0),
        (Path("/etc/mail-admin/secrets"), "directory", 0),
        (Path("/etc/mail-admin/installation.json"), "file", 0),
        (Path("/etc/mail-admin/secrets") / RUNTIME_POLICY_NAME, "file", 0),
    )
    for path, kind, owner in sources:
        metadata = path.lstat()
        expected = (
            stat.S_ISDIR(metadata.st_mode)
            if kind == "directory"
            else stat.S_ISREG(metadata.st_mode)
        )
        if not expected or metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) & 0o027:
            raise ServiceError("unsafe_bind_source", 503)
    RuntimePolicy(
        Path("/etc/mail-admin/installation.json"),
        Path("/etc/mail-admin/secrets") / RUNTIME_POLICY_NAME,
    ).read()
    if (
        deployment["helper_protocol"] != HELPER_PROTOCOL
        or deployment["schema_version"] != SCHEMA_VERSION
    ):
        raise ServiceError("incompatible_release", 503)


def validate_image(deployment: dict[str, Any], docker: Docker) -> None:
    image = docker.inspect(deployment["dashboard_image_id"])
    if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise ServiceError("dashboard_architecture_mismatch", 503)
    if str(image.get("Config", {}).get("User")) != "10001:10001":
        raise ServiceError("dashboard_image_user_mismatch", 503)


def provision_lock(path: Path, owner: int = 0) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != owner
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ServiceError("unsafe_lock_file", 503)


def restrict_data(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ServiceError("invalid_maddy_data_tree", 503)
    for root, directories, files in os.walk(directory, followlinks=False):
        paths = [Path(root) / name for name in directories + files]
        if any(path.is_symlink() for path in paths):
            raise ServiceError("maddy_symlink_requires_review", 409)
        os.chmod(root, 0o700)
        for name in files:
            path = Path(root) / name
            if not path.is_file():
                raise ServiceError("unexpected_maddy_file_type", 409)
            os.chmod(path, 0o600)
