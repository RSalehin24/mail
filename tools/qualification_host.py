"""Observe the installed disposable helper and collect its separately executed host cases."""

import hashlib
import uuid
from pathlib import Path

from helper.maddy import LAUNCHER
from host.render import rendered_files
from shared.files import protected_bytes, protected_json
from tools.qualification_archive import ArtifactArchive
from tools.qualification_bundle import QualificationBundle
from tools.qualification_cases import HOST_CASE_TESTS
from tools.qualification_installation import LIBRARY, UNIT_TARGETS, InstalledFile, InstalledTree

HOST_UNITS = {"mail-admin-helper.service", "mail-admin-paths.service", "docker-ordering.conf",
              "mail-admin.tmpfiles", "journald-mail-admin.conf"}
HOST_SCOPE = "disposable-helper-lifecycle"


def source_manifest(archive: ArtifactArchive) -> dict[str, InstalledFile]:
    return {
        str(LIBRARY / name): InstalledFile(archive.digest(name), member.mode & 0o777)
        for name, member in archive.members.items()
        if member.isfile() and name.startswith(("host/", "helper/", "shared/"))
    }


def source_manifest_digest(bundle: QualificationBundle) -> str:
    from verification_support import canonical_digest

    with ArtifactArchive(bundle.artifacts["host_bundle"]) as archive:
        manifest = source_manifest(archive)
    return canonical_digest({name: vars(value) for name, value in manifest.items()})


def verify_host_installation(bundle: QualificationBundle, host: object) -> dict:
    from verification_support import canonical_digest

    host.verify_consumer()
    if host.record["runtime_image_id"] not in bundle.image.allowed_ids:
        raise ValueError("Disposable consumer differs from the frozen dashboard image")
    configuration = protected_json(Path("/etc/mail-admin/deployment.json"))
    if (configuration["inputs"]["service_uid"], configuration["inputs"]["service_gid"]) != (
        10001, 10001
    ):
        raise ValueError("Disposable helper identity differs from the tested consumer")
    with ArtifactArchive(bundle.artifacts["host_bundle"]) as archive:
        manifest = source_manifest(archive)
        manifest.update(unit_manifest(archive, configuration))
        manifest[LAUNCHER] = InstalledFile(
            archive.digest("host/maddy-launcher"), 0o755
        )
    InstalledTree().verify(manifest)
    return {"source_manifest_sha256": source_manifest_digest(bundle),
            "installed_manifest_sha256": canonical_digest(
                {name: vars(value) for name, value in manifest.items()}),
            "fixture_configuration_sha256": hashlib.sha256(protected_bytes(
                Path("/etc/mail-admin/deployment.json"))).hexdigest()}


def unit_manifest(archive: ArtifactArchive, configuration: dict) -> dict[str, InstalledFile]:
    sources = {Path(name).name: archive.read(name).decode()
               for name in archive.members if name.startswith("host/systemd/")}
    rendered = rendered_files(configuration, sources)
    return {
        UNIT_TARGETS.get(name, "/etc/systemd/system/" + name):
        InstalledFile(hashlib.sha256(rendered[name]).hexdigest(), 0o644)
        for name in HOST_UNITS
    }


def host_report(bundle: QualificationBundle) -> dict | None:
    from check_release import validate_report_time, verified_file
    from verification_support import read_json

    record = next(entry for entry in bundle.context["evidence"]["tests"] if entry["id"] == "T08")
    if record["report"] is None:
        return None
    report = read_json(verified_file(bundle.context, record))
    if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
        or report.get("test_id") != "T08"
        or report.get("environment") != "isolated-linux-amd64"
        or any(report.get(key) != value for key, value in bundle.identity.items())):
        raise ValueError("Host report differs from the frozen artifact set")
    validate_report_time(bundle.context, report)
    verify_host_execution(bundle, report["execution"])
    verify_host_cases(bundle, report)
    return dict(report)


def verify_host_execution(bundle: QualificationBundle, execution: dict) -> None:
    from verification_support import valid_digest

    if (execution.get("scope") != HOST_SCOPE or execution.get("system") != "Linux"
        or execution.get("machine") != "x86_64"
        or execution.get("runtime_image_id") not in bundle.image.allowed_ids
        or execution.get("source_manifest_sha256") != source_manifest_digest(bundle)
        or any(not valid_digest(execution.get(field)) for field in (
            "installed_manifest_sha256", "fixture_configuration_sha256"))):
        raise ValueError("Host report lacks the required observed installation binding")
    for field in ("guest_instance_id", "guest_boot_id"):
        if str(uuid.UUID(execution[field])) != execution[field]:
            raise ValueError("Invalid observed disposable guest identity")


def verify_host_cases(bundle: QualificationBundle, report: dict) -> None:
    from verification_support import indexed

    group = next(group for group in bundle.context["catalog"]["groups"] if group["id"] == "T08")
    cases = indexed(report["cases"])
    if cases.keys() != {case["id"] for case in group["cases"]}:
        raise ValueError("Unexpected, missing or repeated host case")
    for identifier, case in cases.items():
        if set(case) != {"id", "result", "assertions"}:
            raise ValueError("Unexpected host case fields")
        expected = HOST_CASE_TESTS.get(identifier, ())
        if not expected:
            if case != {"id": identifier, "result": "not_run", "assertions": []}:
                raise ValueError("An unimplemented host case cannot become a pass")
        elif set(case["assertions"]) != set(expected) or case["result"] not in {"pass", "fail"}:
            raise ValueError("Host case assertions differ from the reviewed mapping")
        elif case["result"] == "pass" and any(
            result != "pass" for result in case["assertions"].values()
        ):
            raise ValueError("Failed or skipped host assertions cannot become a pass")
    passed = all(case["result"] == "pass" for case in cases.values())
    expected_result = "pass" if passed else "blocked"
    if report.get("result") != expected_result:
        raise ValueError("Host group outcome differs from its actual cases")


def merge_host_report(bundle: QualificationBundle, report: dict) -> None:
    from verification_support import parse_timestamp

    if report["test_id"] != "T08":
        return
    observed = host_report(bundle)
    if observed is None:
        return
    cases = {case["id"]: case for case in observed["cases"]}
    report["cases"] = [cases[case["id"]] if case["id"] in HOST_CASE_TESTS else case
                       for case in report["cases"]]
    report["started_at_utc"] = min(report["started_at_utc"], observed["started_at_utc"],
                                   key=parse_timestamp)
    report["completed_at_utc"] = max(report["completed_at_utc"], observed["completed_at_utc"],
                                     key=parse_timestamp)
    report["execution"]["host"] = observed["execution"]
    passed = all(case["result"] == "pass" for case in report["cases"])
    report["result"] = "pass" if passed else "blocked"
