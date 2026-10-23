"""Archive exact candidate artifacts and leave unexecuted acceptance evidence pending."""

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docs/mail-admin-implementation/tools"))

from verification_support import canonical_digest, specification_digest  # noqa: E402

from tools.fetch_restic import fetch  # noqa: E402
from tools.run_remote import image_snapshot, verify_images  # noqa: E402


def digest(path: Path) -> str:
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def archive_sources(
    destination: Path, directories: tuple[str, ...], extra_files: dict[str, Path] | None = None,
) -> None:
    with tarfile.open(destination, "x:gz") as archive:
        for name in directories:
            source = ROOT / name
            files = (
                sorted(path for path in source.rglob("*") if path.is_file())
                if source.is_dir()
                else [source]
            )
            for path in files:
                if "__pycache__" in path.parts or "node_modules" in path.parts:
                    continue
                info = archive.gettarinfo(str(path), str(path.relative_to(ROOT)))
                info.uid, info.gid, info.uname, info.gname, info.mtime = 0, 0, "root", "root", 0
                with path.open("rb") as reader:
                    archive.addfile(info, reader)
        for name, path in (extra_files or {}).items():
            info = archive.gettarinfo(str(path), name)
            info.uid, info.gid, info.uname, info.gname, info.mtime = 0, 0, "root", "root", 0
            with path.open("rb") as reader:
                archive.addfile(info, reader)


def save_image(destination: Path) -> dict[str, object]:
    images = image_snapshot()
    verify_images(images)
    command = (
        "docker",
        "image",
        "save",
        "--platform",
        "linux/amd64",
        "-o",
        str(destination),
        "mail-admin:candidate",
    )
    subprocess.run(command, check=True, timeout=300)  # noqa: S603 -- fixed local image export.
    if image_snapshot() != images:
        raise RuntimeError("Candidate images changed during release export")
    result = subprocess.run(
        ("docker", "image", "inspect", "--platform", "linux/amd64", "mail-admin:candidate"),  # noqa: S607 -- fixed local read-only observation.
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )  # noqa: S607 -- fixed read-only Docker observation.
    record = json.loads(result.stdout)[0]
    return {key: record[key] for key in ("Id", "Architecture", "Os")}


def pending_configuration(
    image: dict[str, object], inputs: dict[str, object] | None = None
) -> dict[str, object]:
    template = json.loads(
        (
            ROOT / "docs/mail-admin-implementation/verification/runtime-evidence.template.json"
        ).read_text()
    )
    if inputs is not None and not inputs.keys() <= template["inputs"].keys():
        raise ValueError("Unknown deployment input")
    configuration_inputs = {**template["inputs"], **(inputs or {})}
    return {
        "inputs": configuration_inputs,
        "dashboard_image_id": image["Id"],
        "helper_protocol": 1,
        "schema_version": 1,
        "maddy_data_directory": "/opt/maddy/data",
        "maddy_mounts": None,
        "maddy_message_paths": None,
        "baseline_maddy_mounts": None,
        "baseline_served_certificates": None,
        "certificate_state": "/var/lib/mail-admin-certificates/state.json",
        "maddy_tls_target": "/data/tls",
        "tls_generations_directory": "/opt/maddy/tls/generations",
        "consumers": None,
        "certbot_image": None,
        "certbot_live_directory": None,
        "renewal_timeout_seconds": None,
        "transient_resources": {
            "memory_limit_bytes": None,
            "tmpfs_limit_bytes": None,
            "cpu_limit": None,
        },
        "baseline_backup_sources": None,
        "backup_sources": None,
        "backup_ssh_key": None,
        "backup_known_hosts": None,
        "backup_r2": {
            "endpoint": None,
            "credentials_path": None,
            "max_archive_bytes": None,
            "max_bucket_bytes": None,
            "retention_seconds": None,
        },
        "backup_restic": {
            "executable": "/usr/local/lib/mail-admin/bin/restic",
            "executable_sha256": None,
            "password_file": None,
            "state_directory": "/var/lib/mail-admin-backup/jobs",
            "staging_directory": "/var/lib/mail-admin-backup/staging",
            "repository_id": None,
            "installation_id": None,
            "command_timeout_seconds": None,
        },
        "backup_key_path": None,
        "backup_local_retention_seconds": None,
        "backup_resources": {"memory_limit_bytes": None, "cpu_quota_percent": None},
        "rollback_snapshot_ids": [],
        "cutover": {"files": None, "support_files": None},
        "alert_configuration": {
            "endpoint": None,
            "recipient": configuration_inputs["alert_recipient"],
            "credential_reference": None,
        },
    }


def prepare(destination: Path, inputs: dict[str, object] | None = None) -> None:
    destination.mkdir(mode=0o700)
    image = save_image(destination / "dashboard-image.tar")
    restic = fetch("linux_amd64")
    archive_sources(
        destination / "host-bundle.tar.gz",
        ("host", "helper", "shared", "docs/mail-admin-implementation", "docker-compose.yml"),
        {"bin/restic": restic, "bin/restic.json": restic.with_suffix(".json")},
    )
    archive_sources(
        destination / "qualification-suite.tar.gz",
        (
            "tests",
            "tools",
            "backend",
            "helper",
            "host",
            "shared",
            "frontend/src",
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/tsconfig.json",
            "frontend/index.html",
            "requirements.lock",
            "requirements-dev.lock",
            "Makefile",
            "pyproject.toml",
            "Dockerfile",
            "docker-compose.yml",
            "docs/mail-admin-implementation",
        ),
    )
    configuration = destination / "deployment.json"
    configured = pending_configuration(image, inputs)
    configured["backup_restic"]["executable_sha256"] = digest(restic)
    configuration.write_text(json.dumps(configured, indent=2) + "\n")
    template = ROOT / "docs/mail-admin-implementation/verification/runtime-evidence.template.json"
    evidence = json.loads(template.read_text())
    paths = {
        "dashboard_image": "dashboard-image.tar",
        "host_bundle": "host-bundle.tar.gz",
        "deployment_config": "deployment.json",
        "qualification_suite": "qualification-suite.tar.gz",
    }
    evidence["artifacts"] = [
        {"id": key, "path": value, "sha256": digest(destination / value)}
        for key, value in paths.items()
    ]
    identities = {entry["id"]: entry["sha256"] for entry in evidence["artifacts"]}
    evidence.update(
        {
            "release_id": destination.name,
            "specification_sha256": specification_digest(),
            "runner_revision": identities["qualification_suite"],
            "artifact_set_sha256": canonical_digest(identities),
            "inputs": configured["inputs"],
        }
    )
    (destination / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
    (destination / "image-observation.json").write_text(json.dumps(image, indent=2) + "\n")
    print(
        json.dumps(
            {
                "bundle": str(destination),
                "qualification": "blocked",
                "reason": "Required case reports and operator inputs remain pending",
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--inputs", type=Path, help="Non-secret, explicitly selected input values")
    arguments = parser.parse_args()
    inputs = json.loads(arguments.inputs.read_bytes()) if arguments.inputs else None
    if inputs is not None and not isinstance(inputs, dict):
        raise ValueError("Deployment inputs must be an object")
    prepare(arguments.destination, inputs)


if __name__ == "__main__":
    main()
