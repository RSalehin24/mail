"""Execute reviewed catalogue assertions against frozen artifacts; incomplete runs stay blocked."""

import argparse
import copy
import json
import os
import platform
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.qualification_bundle import QualificationBundle  # noqa: E402
from tools.qualification_cases import CASE_TESTS  # noqa: E402
from tools.run_tests import RecordingResult, controller_memory, timestamp  # noqa: E402


def run_case(identifier: str, tests: tuple[str, ...]) -> dict:
    if not tests:
        return {"id": identifier, "result": "not_run", "assertions": []}
    suite = unittest.defaultTestLoader.loadTestsFromNames(tests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult).run(suite)
    passed = (result.wasSuccessful() and result.testsRun == len(tests)
              and set(result.records) == set(tests)
              and all(value == "pass" for value in result.records.values()))
    return {"id": identifier, "result": "pass" if passed else "fail",
            "assertions": result.records}


class CaseAssertions:
    """Reuse a completed assertion only within this frozen qualification execution."""

    def __init__(self, mapping: dict[str, tuple[str, ...]]) -> None:
        self.mapping = mapping
        self.passed: set[str] = set()
        self.started_at = timestamp()

    def run(self, identifier: str) -> dict:
        tests = self.mapping.get(identifier, ())
        missing = tuple(test for test in tests if test not in self.passed)
        if not tests:
            return run_case(identifier, ())
        report = run_case(identifier, missing) if missing else {
            "id": identifier, "result": "pass", "assertions": {},
        }
        if report['result'] != 'pass':
            return report
        if report['assertions'] != dict.fromkeys(missing, 'pass'):
            raise ValueError('Incomplete assertion execution cannot populate the run cache')
        self.passed.update(missing)
        return {'id': identifier, 'result': 'pass',
                'assertions': dict.fromkeys(tests, 'pass')}


def group_report(
    bundle: QualificationBundle, group: dict,
    assertions: dict[str, tuple[str, ...]] | CaseAssertions | None = None,
) -> dict:
    selected = CASE_TESTS if assertions is None else assertions
    runner = selected if isinstance(selected, CaseAssertions) else CaseAssertions(selected)
    started = runner.started_at
    cases = [runner.run(case["id"]) for case in group["cases"]]
    bundle.verify_source()
    bundle.verify_artifacts()
    return {
        "schema_version": 1, "test_id": group["id"], **bundle.identity,
        "environment": group["environment"], "started_at_utc": started,
        "completed_at_utc": timestamp(), "cases": cases,
        "result": "pass" if all(case["result"] == "pass" for case in cases) else "blocked",
        "execution": {"system": platform.system(), "machine": platform.machine(),
                      "docker_engine_architecture": os.environ.get(
                          "MAIL_ADMIN_ENGINE_ARCHITECTURE"
                      ),
                      "runtime_image": os.environ.get("MAIL_ADMIN_RUNTIME_IMAGE"),
                      "qualification_image": os.environ.get("MAIL_ADMIN_QUALIFICATION_IMAGE"),
                      "controller_memory": controller_memory()},
    }


def write_record(path: Path, record: dict) -> None:
    with path.open("x") as writer:
        json.dump(record, writer, indent=2)
        writer.write("\n")


def qualify(evidence: Path) -> dict:
    bundle = load_bundle(evidence)
    directory = evidence.resolve().parent / ("qualification-" + str(uuid.uuid4()))
    directory.mkdir(mode=0o700)
    reports = {}
    assertions = CaseAssertions(CASE_TESTS)
    for group in bundle.context["catalog"]["groups"]:
        if group["id"].startswith("T"):
            reports[group["id"]] = _record_group(bundle, group, (directory, assertions))
    return finish_catalogue(bundle, reports, directory)


def load_bundle(evidence: Path) -> QualificationBundle:
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise ValueError("Catalogue qualification requires isolated Linux/amd64")
    bundle = QualificationBundle(evidence)
    bundle.verify_source()
    cases = {case["id"] for group in bundle.context["catalog"]["groups"]
             for case in group["cases"] if group["id"].startswith("T")}
    if not CASE_TESTS.keys() <= cases:
        raise ValueError("Qualification adapter references an unknown isolated case")
    verify_runtime_image(bundle)
    return bundle


def finish_catalogue(bundle, reports, directory):
    updated = copy.deepcopy(bundle.context['evidence'])
    _update_references(updated, reports, directory)
    write_record(directory.parent / (directory.name + "-evidence.json"), updated)
    cases = [case for report in reports.values() for case in report["cases"]]
    result = {"result": "pass" if all(case["result"] == "pass" for case in cases) else "blocked",
              "passed_cases": sum(case["result"] == "pass" for case in cases),
              "required_cases": len(cases), "reports": str(directory), **bundle.identity}
    write_record(directory / "result.json", result)
    return result


def catalogue_inventory(evidence: Path) -> dict:
    from tools.run_tests import source_digest

    bundle = load_bundle(evidence)
    return {**bundle.identity, 'source_sha256': source_digest(), 'groups': {
        group['id']: [case['id'] for case in group['cases']]
        for group in bundle.context['catalog']['groups'] if group['id'].startswith('T')
    }}


def qualify_group(evidence: Path, selection: tuple[str, Path]) -> dict:
    from tools.catalogue_reports import report_directory

    identifier, selected = selection
    bundle = load_bundle(evidence)
    groups = {group['id']: group for group in bundle.context['catalog']['groups']
              if group['id'].startswith('T')}
    if identifier not in groups:
        raise ValueError('An existing isolated catalogue group is required')
    directory = report_directory(evidence, selected)
    return _record_group(bundle, groups[identifier], (directory, CaseAssertions(CASE_TESTS)))


def assemble_groups(evidence: Path, selected: Path) -> dict:
    from tools.catalogue_reports import report_directory, validate_group

    bundle = load_bundle(evidence)
    directory = report_directory(evidence, selected)
    reports = {}
    for group in bundle.context['catalog']['groups']:
        if group['id'].startswith('T'):
            report = json.loads((directory / (group['id'] + '.json')).read_bytes())
            validate_group(bundle, group, report)
            reports[group['id']] = report
    bundle.verify_source()
    bundle.verify_artifacts()
    return finish_catalogue(bundle, reports, directory)


def _record_group(
    bundle: QualificationBundle, group: dict, output: tuple[Path, CaseAssertions],
) -> dict:
    from tools.qualification_host import merge_host_report

    directory, assertions = output
    report = group_report(bundle, group, assertions)
    merge_host_report(bundle, report)
    write_record(directory / (group["id"] + ".json"), report)
    return report


def _update_references(evidence: dict, reports: dict, directory: Path) -> None:
    from verification_support import file_digest

    for record in evidence["tests"]:
        if record["id"] in reports:
            record.update(report=directory.name + "/" + record["id"] + ".json",
                          report_sha256=file_digest(directory / (record["id"] + ".json")))


def verify_runtime_image(bundle: QualificationBundle) -> None:
    from host.docker import Docker
    from tools.run_remote import image_observation
    from tools.run_tests import source_digest

    runtime = os.environ["MAIL_ADMIN_RUNTIME_IMAGE"]
    qualification = os.environ["MAIL_ADMIN_QUALIFICATION_IMAGE"]
    observed = json.loads(Docker().run(("image", "inspect", "--platform", "linux/amd64", runtime)))
    if len(observed) != 1 or observed[0]["Id"] not in bundle.image.allowed_ids:
        raise ValueError("Runtime fixture image differs from the qualified image archive")
    runtime_files, suite_files = image_observation(runtime), image_observation(qualification)
    if runtime_files["runtime_sha256"] != suite_files["runtime_sha256"] or (
        suite_files["source_sha256"] != source_digest()
    ):
        raise ValueError("Runtime image files differ from the executing frozen suite")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inventory', action='store_true')
    mode.add_argument('--group')
    mode.add_argument('--assemble', action='store_true')
    parser.add_argument('--report-directory', type=Path)
    arguments = parser.parse_args()
    result = selected_run(arguments)
    print(json.dumps(result))
    return 0 if arguments.inventory or result["result"] == "pass" else 1


def selected_run(arguments):
    if arguments.inventory:
        if arguments.report_directory is not None:
            raise ValueError('An inventory cannot select an output directory')
        return catalogue_inventory(arguments.evidence)
    if arguments.group or arguments.assemble:
        if arguments.report_directory is None:
            raise ValueError('Group execution requires an explicit report directory')
        if arguments.group:
            return qualify_group(arguments.evidence, (arguments.group, arguments.report_directory))
        return assemble_groups(arguments.evidence, arguments.report_directory)
    if arguments.report_directory is not None:
        raise ValueError('An output directory requires an explicit group or assembly')
    return qualify(arguments.evidence)


if __name__ == "__main__":
    raise SystemExit(main())
