"""Run destructive lifecycle assertions only inside the exact owned disposable guest."""

import argparse
import copy
import json
import os
import platform
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.host_support import GuestHelper  # noqa: E402
from tools.qualification_bundle import QualificationBundle  # noqa: E402
from tools.qualification_cases import HOST_CASE_TESTS  # noqa: E402
from tools.qualification_host import HOST_SCOPE, verify_host_installation  # noqa: E402
from tools.run_qualification import _update_references, group_report, write_record  # noqa: E402


def qualify(evidence: Path, identifier: str = 'T08') -> dict:
    host = GuestHelper.load()
    bundle = QualificationBundle(evidence)
    bundle.verify_source()
    before = verify_host_installation(bundle, host)
    os.environ['MAIL_ADMIN_HOST_EVIDENCE'] = str(evidence.resolve())
    group = host_group(bundle, identifier)
    report = group_report(bundle, group, HOST_CASE_TESTS)
    directory = evidence.resolve().parent / ("host-qualification-" + str(uuid.uuid4()))
    directory.mkdir(mode=0o700)
    write_record(directory / "assertion-results.json", report)
    if verify_host_installation(bundle, host) != before:
        raise ValueError("Installed helper files changed during lifecycle checks")
    report["execution"] = {
        "scope": HOST_SCOPE, "system": platform.system(), "machine": platform.machine(),
        "guest_instance_id": host.record["instance_id"],
        "guest_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "runtime_image_id": host.record["runtime_image_id"],
        "virtualization": host.command(('systemd-detect-virt', '--vm')), **before,
    }
    write_record(directory / (identifier + '.json'), report)
    updated = copy.deepcopy(bundle.context["evidence"])
    _update_references(updated, {identifier: report}, directory)
    path = directory.parent / (directory.name + "-evidence.json")
    write_record(path, updated)
    return {"result": report["result"], "reports": str(directory), "evidence": str(path),
            "passed_cases": sum(case["result"] == "pass" for case in report["cases"]),
            "required_cases": len(report["cases"]), **bundle.identity}


def lifecycle_group(bundle: QualificationBundle) -> dict:
    group = next(group for group in bundle.context["catalog"]["groups"] if group["id"] == "T08")
    # Observe boot recovery before any later test can restart a missing consumer.
    cases = sorted(group['cases'], key=lambda case: case['id'] != 'T08.04')
    return {**group, 'cases': cases}


def host_group(bundle: QualificationBundle, identifier: str) -> dict:
    if identifier == 'T08':
        return lifecycle_group(bundle)
    selected = next((group for group in bundle.context['catalog']['groups']
                     if group['id'] == identifier), None)
    if selected is None or not any(case['id'] in HOST_CASE_TESTS for case in selected['cases']):
        raise ValueError('An existing registered host qualification group is required')
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument('--group', default='T08')
    arguments = parser.parse_args()
    result = qualify(arguments.evidence, arguments.group)
    print(json.dumps(result))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
