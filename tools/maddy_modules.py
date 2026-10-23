"""Run Maddy and runtime modules in fresh bounded controllers; retain raw outcomes."""

import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path

from tools.resources import fixture_budget
from tools.run_tests import timestamp

MODULE_TIMEOUT_SECONDS = 900

INVENTORY = """import json, os
from pathlib import Path
from tools.run_tests import source_digest
suite=os.environ['MAIL_ADMIN_MODULE_SUITE']
root=Path('tests')/suite
files=[p for p in root.rglob('test*.py') if p.is_file()]
if any(p.parent!=root for p in files): raise ValueError('Nested test module is unsupported')
print(json.dumps({'source_sha256':source_digest(), 'suite':suite,
                  'modules':sorted(p.stem for p in files)}))
"""


def module_inventory(image: str, docker, suite: str = "maddy") -> dict:
    if suite not in {"maddy", "runtime"}:
        raise ValueError("Unsupported isolated module suite")
    response = docker((
        "run", "--rm", "--platform", "linux/amd64", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--memory", "64m", "--memory-swap", "64m", "--pids-limit", "32",
        "--label", "mail-admin.fixture=true", "-e", "MAIL_ADMIN_MODULE_SUITE=" + suite,
        image, "python", "-c", INVENTORY,
    ))
    inventory = json.loads(response)
    if inventory.get("suite", "maddy") != suite:
        raise ValueError("Module inventory suite mismatch")
    modules = inventory["modules"]
    if not isinstance(modules, list) or not 1 <= len(modules) <= 128 or (
        len(set(modules)) != len(modules)
    ) or any(not isinstance(name, str) or re.fullmatch(r"test_[a-z0-9_]+", name) is None
             for name in modules):
        raise ValueError("Invalid isolated Maddy module inventory")
    return dict(inventory)


def run_maddy(command: tuple[str, ...], root: Path, docker) -> int:
    return run_modules(command, (root, "maddy"), docker)


def run_modules(command: tuple[str, ...], context: tuple[Path, str], docker) -> int:
    root, suite = context
    inventory = module_inventory(command[-1], docker, suite)
    directory = root / "build" / (suite + "-modules-" + str(uuid.uuid4()))
    directory.mkdir(mode=0o700)
    started, outcomes = timestamp(), {}
    for name in inventory["modules"]:
        selected = Path("build") / directory.name / name
        print("Isolated " + suite + " module: " + name, flush=True)
        result = subprocess.run(  # noqa: S603, S607 -- validated module, pinned fixture argv.
            ("docker",) + command + ("python", "tools/run_tests.py", suite, "--module", name,
                                    "--report-directory", str(selected)), check=False,
            timeout=MODULE_TIMEOUT_SECONDS,
        )
        outcomes[name] = result.returncode
    report = aggregate_modules(directory, (inventory, outcomes, started))
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    (root / "build" / report_name(suite)).write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["result"] == "pass" else 1


def read_module(path: Path) -> dict:
    try:
        report = json.loads(path.read_bytes())
    except (OSError, ValueError) as error:
        return {"result": "unavailable", "diagnostic": type(error).__name__}
    if not isinstance(report, dict):
        return {"result": "invalid", "diagnostic": "Report must be an object"}
    if not isinstance(report.get("tests"), dict) or not isinstance(
        report.get("controller_memory"), dict
    ):
        return {"result": "invalid", "diagnostic": "Missing test/memory objects"}
    return report


def aggregate_modules(directory: Path, execution: tuple[dict, dict, str]) -> dict:
    inventory, outcomes, started = execution
    suite = inventory.get("suite", "maddy")
    modules = {name: read_module(directory / name / report_name(suite))
               for name in inventory["modules"]}
    valid = module_bindings(modules, inventory)
    tests = {identifier: outcome for report in modules.values()
             for identifier, outcome in report.get("tests", {}).items()}
    duplicate = len(tests) != sum(len(report.get("tests", {})) for report in modules.values())
    passed = valid and not duplicate and set(outcomes) == set(modules) and all(
        outcomes[name] == 0 and report.get("result") == "pass" and report.get("tests")
        and all(value == "pass" for value in report["tests"].values())
        for name, report in modules.items()
    )
    return {
        **module_metadata(modules), "schema_version": 1, "suite": suite, "module": None,
        "scope": suite + " application modules in sequential controllers; not qualification",
        "source_sha256": inventory["source_sha256"], "started_at_utc": started,
        "completed_at_utc": timestamp(), "result": "pass" if passed else "fail",
        "tests": tests, "module_reports": module_references(directory, outcomes, suite),
        "production_qualification": "blocked",
    }


def module_bindings(modules: dict, inventory: dict) -> bool:
    keys = ("python", "system", "machine", "qualification_image", "runtime_image",
            "docker_engine_architecture")
    first = next(iter(modules.values()))
    if first.get("system") != "Linux" or first.get("machine") not in {"x86_64", "amd64"}:
        return False
    if any(not isinstance(first.get(key), str) or not first[key] for key in keys):
        return False
    return all(
        report.get("source_sha256") == inventory["source_sha256"]
        and report.get("suite") == inventory.get("suite", "maddy")
        and report.get("module") == name
        and all(report.get(key) == first.get(key) for key in keys)
        and isinstance(report.get("tests"), dict)
        and report.get("controller_memory", {}).get("limit_bytes")
        == fixture_budget(inventory.get("suite", "maddy")).controller_mib * 1024**2
        for name, report in modules.items()
    )


def module_metadata(modules: dict) -> dict:
    first = next(iter(modules.values()))
    keys = ("python", "system", "machine", "qualification_image", "runtime_image",
            "docker_engine_architecture")
    memory = [report.get("controller_memory", {}) for report in modules.values()]
    observed = all(record.get("state") == "observed" for record in memory)
    return {**{key: first.get(key) for key in keys}, "controller_memory": {
        "state": "observed" if observed else "unavailable",
        "limit_bytes": memory[0].get("limit_bytes") if observed else None,
        "peak_bytes": max(record["peak_bytes"] for record in memory) if observed else None,
        "scope": "maximum among sequential qualification controllers; not whole host",
    }}


def report_name(suite: str) -> str:
    return ("linux-" if suite == "runtime" else "") + suite + "-checks.json"


def module_references(directory: Path, outcomes: dict, suite: str = "maddy") -> dict:
    references = {}
    for name, code in outcomes.items():
        path = directory / name / report_name(suite)
        references[name] = {"report": str(path), "controller_exit_code": code,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                            if path.is_file() else None}
    return references
