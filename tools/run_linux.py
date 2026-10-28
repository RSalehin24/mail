"""Run real Maddy tests on a genuine local Linux volume, outside the web runtime image."""

import argparse
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.catalogue_modules import run_catalogue  # noqa: E402
from tools.maddy_modules import run_maddy, run_modules  # noqa: E402
from tools.resources import fixture_budget, require_fixture_disk  # noqa: E402
from tools.run_tests import validate_module  # noqa: E402


def docker(arguments: tuple[str, ...]) -> str:
    # Only this local test driver supplies arguments; no production endpoint is accepted.
    result = subprocess.run(  # noqa: S603 -- trusted local fixture driver constructs every argv.
        ("docker",) + arguments, capture_output=True, text=True, timeout=600, check=True
    )
    return result.stdout.strip()


@contextmanager
def pinned_runtime_image(image_id: str) -> Iterator[str]:
    reference = "mail-admin:fixture-" + str(uuid.uuid4())
    docker(("image", "tag", image_id, reference))
    try:
        yield reference
    finally:
        docker(("image", "rm", reference))


def suite_command(
    suite: str,
    evidence: Path | None,
    module: str | None = None,
) -> tuple[str, ...]:
    validate_module(suite, module)
    if module is not None:
        if evidence is not None:
            raise ValueError("Partial module checks cannot consume qualification evidence")
        report = "build/" + suite + "-partial-" + str(uuid.uuid4()) + "/" + module
        return (
            "python",
            "tools/run_tests.py",
            suite,
            "--module",
            module,
            "--report-directory",
            report,
        )
    if suite != "qualification":
        if evidence is not None:
            raise ValueError("An evidence bundle is only accepted for catalogue qualification")
        return "python", "tools/run_tests.py", suite
    if evidence is None or not evidence.is_file():
        raise ValueError("Catalogue qualification requires a frozen evidence bundle")
    selected = evidence.resolve()
    if not selected.is_relative_to(ROOT / "build"):
        raise ValueError("Qualification bundle must be inside the mounted build directory")
    return (
        "python",
        "tools/run_qualification.py",
        str(Path("/app/build") / selected.relative_to(ROOT / "build")),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("unit", "integration", "maddy", "runtime", "acme", "qualification"),
        default="maddy",
    )
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--module", help="Run one Maddy/runtime/ACME module; partial suite")
    parser.add_argument("--resume", type=Path, help="Resume a matching interrupted catalogue")
    arguments = parser.parse_args()
    if arguments.resume is not None and arguments.suite != "qualification":
        parser.error("--resume requires --suite qualification")
    entrypoint = suite_command(arguments.suite, arguments.evidence, arguments.module)
    require_fixture_disk(ROOT)
    context = json.loads(docker(("context", "inspect")))[0]
    endpoint = os.environ.get("DOCKER_HOST", context["Endpoints"]["docker"]["Host"])
    if not endpoint.startswith("unix://"):
        raise RuntimeError("Refusing a remote Docker engine")
    from tools.run_remote import verify_images

    image = json.loads(docker(("image", "inspect", "mail-admin:qualification")))[0]
    runtime = json.loads(docker(("image", "inspect", "mail-admin:candidate")))[0]
    verify_images(
        {"mail-admin:qualification": image["Id"], "mail-admin:candidate": runtime["Id"]},
        transferred_source(ROOT),
    )
    volume = "mail-admin-qualification-" + str(uuid.uuid4())
    docker(("volume", "create", "--label", "mail-admin.fixture=true", volume))
    mountpoint = json.loads(docker(("volume", "inspect", volume)))[0]["Mountpoint"]
    root = Path(__file__).resolve().parents[1]
    engine = json.loads(docker(("info", "--format", "{{json .}}")))
    controller_memory = str(fixture_budget(arguments.suite).controller_mib) + "m"
    try:
        command = (
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--memory",
            controller_memory,
            "--memory-swap",
            controller_memory,
            "--cpus",
            "0.5",
            "--pids-limit",
            "128",
            "--network",
            "host",
            "--label",
            "mail-admin.fixture=true",
            "--mount",
            f"type=volume,src={volume},dst=/fixtures",
            "--mount",
            "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock",
            "--mount",
            f"type=bind,src={root / 'build'},dst=/app/build",
            "-e",
            "MAIL_ADMIN_FIXTURE_ROOT=/fixtures",
            "-e",
            "MAIL_ADMIN_REPORT_UID=" + str(os.getuid()),
            "-e",
            "MAIL_ADMIN_REPORT_GID=" + str(os.getgid()),
            "-e",
            "TMPDIR=/fixtures",
            "-e",
            "MAIL_ADMIN_FIXTURE_DAEMON_ROOT=" + mountpoint,
            "-e",
            "MAIL_ADMIN_QUALIFICATION_IMAGE=" + image["Id"],
            "-e",
            "MAIL_ADMIN_ENGINE_ARCHITECTURE=" + engine["Architecture"],
            "-e",
            "MAIL_ADMIN_RUNTIME_IMAGE=" + runtime["Id"],
            "-e",
            "MAIL_ADMIN_REPORT_PREFIX=linux-"
            if arguments.suite != "maddy"
            else "MAIL_ADMIN_REPORT_PREFIX=",
        )
        # Stream assertion output; fixture secrets are only passed to non-TTY CLI stdin.
        with (
            pinned_runtime_image(runtime["Id"]) as reference,
            pinned_runtime_image(image["Id"]) as controller_reference,
        ):
            command += (
                "-e",
                "MAIL_ADMIN_RUNTIME_REFERENCE=" + reference,
                controller_reference,
            )
            if arguments.suite == "maddy" and arguments.module is None:
                code = run_maddy(command, root, docker)
            elif arguments.suite in {"runtime", "acme"} and arguments.module is None:
                code = run_modules(command, (root, arguments.suite), docker)
            elif arguments.suite == "qualification":
                code = run_catalogue(
                    command,
                    (
                        root,
                        arguments.evidence,
                        entrypoint,
                        arguments.resume,
                    ),
                    docker,
                )
            else:
                result = subprocess.run(  # noqa: S603, S607 -- fixed pinned fixture command.
                    ("docker",) + command + entrypoint, check=False
                )
                code = result.returncode
        if code:
            raise SystemExit(code)
    finally:
        docker(("volume", "rm", volume))


def transferred_source(root: Path) -> str | None:
    if (root / "Dockerfile").is_file():
        return None
    value = json.loads((root / "images.json").read_bytes())["source_sha256"]
    if not isinstance(value, str):
        raise ValueError("Invalid transferred qualification source identity")
    return value


if __name__ == "__main__":
    main()
