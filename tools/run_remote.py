"""Run only isolated fixtures on the explicitly authorized native host; never deploy services."""

import argparse
import gzip
import hashlib
import json
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.fixture_images import ACME_IMAGES  # noqa: E402
from tools.maddy_modules import MODULE_TIMEOUT_SECONDS, module_inventory  # noqa: E402
from tools.resources import FIXTURE_DISK_RESERVE_BYTES, fixture_budget  # noqa: E402
from tools.run_tests import source_digest  # noqa: E402

IMAGE_OBSERVATION = """import hashlib,json
from pathlib import Path
files = {
 str(p):hashlib.sha256(p.read_bytes()).hexdigest()
 for name in ('backend','shared','static') for p in Path(name).rglob('*')
 if p.is_file() and '__pycache__' not in p.parts
}
source = None
if Path('tools/run_tests.py').is_file():
 from tools.run_tests import source_digest
 source = source_digest()
print(json.dumps({
 'runtime_sha256':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
 'source_sha256':source
}))
"""


def command(arguments: tuple[str, ...], directory: Path | None = None) -> str:
    result = subprocess.run(  # noqa: S603 -- only fixed argv and validated fixture references.
        arguments, check=True, capture_output=True, text=True, timeout=600, cwd=directory
    )
    return result.stdout.strip()


def ssh(host: str, arguments: tuple[str, ...]) -> str:
    return command(
        (
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            host,
            shlex.join(arguments),
        )
    )


def image_snapshot() -> dict[str, str]:
    names = ("mail-admin:candidate", "mail-admin:qualification")
    records = json.loads(command(("docker", "image", "inspect") + names))
    if any(record["Architecture"] != "amd64" or record["Os"] != "linux" for record in records):
        raise RuntimeError("Isolated images must be Linux/amd64")
    return {name: record["Id"] for name, record in zip(names, records, strict=True)}


def image_observation(image: str) -> dict[str, str | None]:
    response = command(
        (
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--memory",
            "192m",
            "--memory-swap",
            "192m",
            "--label",
            "mail-admin.fixture=true",
            image,
            "python",
            "-c",
            IMAGE_OBSERVATION,
        )
    )
    return dict(json.loads(response))


def verify_images(images: dict[str, str], expected_source: str | None = None) -> None:
    if expected_source is not None and re.fullmatch(r'[0-9a-f]{64}', expected_source) is None:
        raise ValueError('Invalid transferred qualification source identity')
    runtime = image_observation(images["mail-admin:candidate"])
    qualification = image_observation(images["mail-admin:qualification"])
    if qualification["source_sha256"] != (expected_source or source_digest()):
        raise RuntimeError("Qualification image is stale; rebuild it before remote testing")
    if runtime["runtime_sha256"] != qualification["runtime_sha256"]:
        raise RuntimeError("Runtime image differs from the qualified build; rebuild both images")


def local_archive(path: Path) -> str:
    images, source = image_snapshot(), source_digest()
    verify_images(images)
    raw = path.with_suffix("")
    command(
        (
            "docker",
            "image",
            "save",
            "--platform",
            "linux/amd64",
            "-o",
            str(raw),
            "mail-admin:candidate",
            "mail-admin:qualification",
            "foxcpp/maddy@sha256:2b0c7e0e06b5e6e9e0738a2d6a826dc03b945e864db69288efd7fad4a62ee1e8",
            *ACME_IMAGES,
        )
    )
    if images != image_snapshot() or source != source_digest():
        raise RuntimeError("Images or source changed during export; rebuild and retry")
    inventory = module_inventory(images['mail-admin:qualification'],
                                 lambda arguments: command(('docker',) + arguments))
    runtime_inventory = module_inventory(
        images['mail-admin:qualification'],
        lambda arguments: command(('docker',) + arguments), 'runtime',
    )
    if any(record['source_sha256'] != source for record in (inventory, runtime_inventory)):
        raise RuntimeError('Frozen module inventory differs from the exported source')
    (path.parent / "images.json").write_text(json.dumps({
        "images": images, "source_sha256": source, 'maddy_modules': inventory['modules'],
        'runtime_modules': runtime_inventory['modules'],
    }, indent=2) + "\n")
    with raw.open("rb") as reader, gzip.open(path, "xb", compresslevel=1) as writer:
        shutil.copyfileobj(reader, writer)
    with path.open("rb") as reader:
        digest = hashlib.file_digest(reader, "sha256").hexdigest()
    return digest


def live_observation(record: dict[str, object]) -> dict[str, object]:
    fields = {key: record[key] for key in ("Id", "Image", "Config", "HostConfig")}
    mounts = sorted(record["Mounts"], key=lambda mount: mount["Destination"])
    fields["Mounts"] = mounts
    digests = {
        key: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        for key, value in fields.items()
    }
    return {
        "fields": digests,
        "started_at": record["State"]["StartedAt"],
        "running": record["State"]["Running"],
    }


def fixture_support_files() -> tuple[Path, ...]:
    return tuple(ROOT / "tools" / name for name in (
        "run_linux.py", "resources.py", "maddy_modules.py", "run_tests.py",
        'run_remote.py', 'fixture_images.py',
        'catalogue_modules.py',
    ))


class NativeRun:
    def __init__(self, host: str, paths: tuple[Path, str]) -> None:
        self.host, (self.local, self.remote) = host, paths
        self.outcomes = {
            suite: "not_run" for suite in ("unit", "integration", "maddy", "runtime", 'acme')
        }
        self.collected = False

    def execute(self, suite: str) -> None:
        self.outcomes[suite] = "unknown"
        arguments = ("sudo", "-n", "python3", self.remote + "/tools/run_linux.py", "--suite", suite)
        try:
            result = subprocess.run(  # noqa: S603 -- validated SSH target; fixed fixture driver.
            (
                "/usr/bin/ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                self.host,
                shlex.join(arguments),
            ),
            capture_output=True,
            text=True,
            timeout=self._timeout(suite),
            check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = b''.join(value.encode() if isinstance(value, str) else value
                              for value in (error.stdout, error.stderr) if value)
            (self.local / (suite + '.log')).write_bytes(output)
            raise
        (self.local / (suite + ".log")).write_text(result.stdout + result.stderr)
        self.outcomes[suite] = "pass" if result.returncode == 0 else "fail"

    def _timeout(self, suite: str) -> int:
        if suite not in {'maddy', 'runtime'}:
            return MODULE_TIMEOUT_SECONDS
        modules = json.loads((self.local / 'images.json').read_bytes())[suite + '_modules']
        if (not isinstance(modules, list) or not 1 <= len(modules) <= 128
            or any(not isinstance(name, str) or re.fullmatch(r'test_[a-z0-9_]+', name) is None
                   for name in modules) or len(set(modules)) != len(modules)):
            raise ValueError('A validated frozen module inventory is required')
        # Every module retains its existing bound; the SSH supervisor covers their sum.
        return len(modules) * MODULE_TIMEOUT_SECONDS + 60

    def collect(self) -> None:
        ssh(self.host, ("sudo", "-n", "chmod", "-R", "a+rX", self.remote + "/build"))
        command(
            (
                "scp",
                "-B",
                "-r",
                "-o",
                "StrictHostKeyChecking=yes",
                self.host + ":" + self.remote + "/build",
                str(self.local / "reports"),
            )
        )
        self.collected = True

    @property
    def failures(self) -> list[str]:
        return [suite for suite, outcome in self.outcomes.items() if outcome != "pass"]

    def result(self, intact: bool) -> str:
        return "pass" if intact and not self.failures and self.collected else "fail"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    arguments = parser.parse_args()
    if re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", arguments.host) is None:
        raise ValueError("Explicit SSH user@host required")
    identifier = "mail-admin-qualification-" + str(uuid.uuid4())
    root = Path(__file__).resolve().parents[1]
    local = root / "build" / identifier
    local.mkdir(mode=0o700)
    archive = local / "images.tar.gz"
    digest = local_archive(archive)
    home = ssh(arguments.host, ("python3", "-c", "from pathlib import Path; print(Path.home())"))
    if re.fullmatch(r"/home/[a-zA-Z0-9_-]+", home) is None:
        raise RuntimeError("Unexpected remote home directory")
    prefix = home + "/" + identifier + "-"
    remote = ssh(arguments.host, ("mktemp", "-d", prefix + "XXXXXX"))
    if re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9]{6}", remote) is None:
        raise RuntimeError("Unexpected remote fixture path")
    ssh(arguments.host, ("mkdir", remote + "/build", remote + "/tools"))
    copies = ((archive, remote + "/images.tar.gz"),
              (local / 'images.json', remote + '/images.json')) + tuple(
        (source, remote + "/tools/" + source.name) for source in fixture_support_files()
    )
    for source, destination in copies:
        command(
            (
                "scp",
                "-B",
                "-o",
                "StrictHostKeyChecking=yes",
                str(source),
                arguments.host + ":" + destination,
            )
        )
    actual = ssh(arguments.host, ("sha256sum", remote + "/images.tar.gz")).split()[0]
    if actual != digest:
        raise RuntimeError("Remote image archive digest mismatch")
    before = ssh(arguments.host, ("sudo", "-n", "docker", "inspect", "maddy"))
    baseline = live_observation(json.loads(before)[0])
    (local / "live-before.json").write_text(json.dumps(baseline, indent=2) + "\n")
    ssh(arguments.host, ("sudo", "-n", "docker", "load", "-i", remote + "/images.tar.gz"))
    native = NativeRun(arguments.host, (local, remote))
    try:
        for suite in ("unit", "integration", "maddy", "runtime", 'acme'):
            print("Native isolated suite: " + suite, flush=True)
            capacity = ssh(
                arguments.host,
                (
                    "python3",
                    "-c",
                    "import json,shutil\n"
                    "m={x.split(':')[0]:int(x.split()[1])*1024 for x in open('/proc/meminfo') "
                    "if x.startswith(('MemAvailable:','MemTotal:'))}\n"
                    "m['disk_free_bytes']=shutil.disk_usage('/').free\n"
                    "print(json.dumps(m))\n",
                ),
            )
            available = json.loads(capacity)
            if (
                available["MemAvailable"] < fixture_budget(suite).minimum_available_bytes
                or available["disk_free_bytes"] < FIXTURE_DISK_RESERVE_BYTES
            ):
                native.outcomes[suite] = "insufficient_spare_capacity"
                break
            native.execute(suite)
        native.collect()
    finally:
        after = ssh(arguments.host, ("sudo", "-n", "docker", "inspect", "maddy"))
        current = json.loads(after)[0]
        observed = live_observation(current)
        (local / "live-after.json").write_text(json.dumps(observed, indent=2) + "\n")
        intact = baseline == observed and observed["running"] is True
        result = {
            "scope": "isolated native fixtures; not production acceptance",
            "live_container_id": current["Id"],
            "live_image_id": current["Image"],
            "live_maddy_unchanged": intact,
            "remote_directory": remote,
            "archive_sha256": digest,
            "suite_outcomes": native.outcomes,
            "reports_collected": native.collected,
            "failed_suites": native.failures,
            "result": native.result(intact),
        }
        (local / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    if native.result(intact) != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
