"""Small synthetic archives for qualification-adapter regressions; never release evidence."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

from tools.qualification_bundle import ROOT


def write_archive(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "x") as archive:
        for name, value in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(value), 0o644
            archive.addfile(info, io.BytesIO(value))


def image_files() -> tuple[dict[str, bytes], str]:
    configuration = json.dumps({"architecture": "amd64", "os": "linux",
                                "config": {"User": "10001:10001"}}).encode()
    identity = "sha256:" + hashlib.sha256(configuration).hexdigest()
    descriptor = {"digest": identity, "size": len(configuration)}
    manifest = json.dumps({"schemaVersion": 2, "config": descriptor, "layers": []}).encode()
    manifest_id = "sha256:" + hashlib.sha256(manifest).hexdigest()
    index = {"schemaVersion": 2, "manifests": [{
        "digest": manifest_id, "size": len(manifest),
        "platform": {"architecture": "amd64", "os": "linux"},
    }]}
    return {
        "index.json": json.dumps(index).encode(),
        "blobs/sha256/" + identity[7:]: configuration,
        "blobs/sha256/" + manifest_id[7:]: manifest,
    }, manifest_id


def write_bundle(root: Path, source_files: dict[str, bytes]) -> Path:
    from verification_support import canonical_digest, file_digest, specification_digest

    evidence = json.loads((ROOT / "docs/mail-admin-implementation/verification/"
                           "runtime-evidence.template.json").read_bytes())
    files, _ = image_files()
    write_archive(root / "dashboard.tar", files)
    host = {name: value for name, value in source_files.items() if name.startswith(
        ("host/", "helper/", "shared/", "docs/mail-admin-implementation/")
    ) or name == "docker-compose.yml"}
    host.update({"bin/restic": b"synthetic restic", "bin/restic.json": b"{}"})
    write_archive(root / "host.tar", host)
    write_archive(root / "suite.tar", source_files)
    configuration = {"inputs": evidence["inputs"], "backup_restic": {
        "executable_sha256": hashlib.sha256(host["bin/restic"]).hexdigest()
    }}
    (root / "deployment.json").write_text(json.dumps(configuration))
    paths = {"dashboard_image": "dashboard.tar", "host_bundle": "host.tar",
             "qualification_suite": "suite.tar", "deployment_config": "deployment.json"}
    evidence["artifacts"] = [
        {"id": key, "path": value, "sha256": file_digest(root / value)}
        for key, value in paths.items()
    ]
    identities = {record["id"]: record["sha256"] for record in evidence["artifacts"]}
    evidence.update(artifact_set_sha256=canonical_digest(identities), release_id="synthetic",
                    specification_sha256=specification_digest(),
                    runner_revision=identities["qualification_suite"])
    path = root / "evidence.json"
    path.write_text(json.dumps(evidence))
    return path
