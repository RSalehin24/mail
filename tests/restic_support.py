"""Real pinned Restic with a private local repository for deterministic Linux recovery tests."""

import hashlib
import os
import secrets
from pathlib import Path

from host.restic_process import ResticProcess, resource_environment
from host.restic_repository import ResticRepository
from shared.errors import ServiceError
from shared.files import atomic_json


class RestoredFixtureDocker:
    def __init__(self, fixture):
        self.fixture = fixture
        self.executable = "/usr/bin/docker"

    def run(self, arguments):
        rewritten = []
        for argument in arguments:
            if argument.startswith("type=bind,src="):
                source = argument.split(",")[1].removeprefix("src=")
                argument = argument.replace(
                    "src=" + source + ",", "src=" + self.fixture.daemon_path(Path(source)) + ",", 1,
                )
            rewritten.append(argument)
        return self.fixture.command(tuple(rewritten)).stdout.strip()

    def inspect(self, name):
        import json

        return json.loads(self.run(("inspect", name)))[0]


class LocalResticProcess(ResticProcess):
    def environment(self):
        return {
            "RESTIC_REPOSITORY": self.configuration["fixture_repository"],
            "RESTIC_PASSWORD_FILE": str(self.paths.password),
            **resource_environment(),
        }


class LocalBudget:
    def __init__(self, directory, limit):
        self.directory, self.limit = directory, limit

    def require(self, incoming_bytes):
        current = sum(path.stat().st_size for path in self.directory.rglob("*") if path.is_file())
        if current + incoming_bytes > self.limit:
            raise ServiceError("fixture_repository_budget_exceeded", 503)


def local_repository(directory, staging, clock):
    executable = Path("/usr/local/bin/restic")
    binary_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    state, repository = directory / "restic-jobs", directory / "restic-repository"
    for path in (state, repository):
        path.mkdir(mode=0o700)
    (state / "command.lock").touch(mode=0o600)
    password = directory / "restic-password.key"
    password.write_text(secrets.token_urlsafe(48))
    password.chmod(0o600)
    configured = {
        "inputs": {"backup_destination": "s3://fixture-bucket/mail-admin/"},
        "fixture_repository": str(repository),
        "backup_r2": {
            "endpoint": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
            "max_archive_bytes": 64 * 1024 * 1024,
            "max_bucket_bytes": 256 * 1024 * 1024, "retention_seconds": 7 * 86400,
        },
        "backup_restic": {
            "executable": str(executable), "executable_sha256": binary_hash,
            "password_file": str(password), "state_directory": str(state),
            "staging_directory": str(staging), "repository_id": None,
            "installation_id": "native-fixture", "command_timeout_seconds": 60,
        },
    }
    # Local fixtures never load cloud credentials or make an S3 request.
    configured["backup_r2"]["credentials_path"] = str(directory / "unused-private-credential")
    budget = LocalBudget(repository, 256 * 1024 * 1024)
    process = LocalResticProcess(configured, os.getuid())
    initialized = ResticRepository(process, clock, budget)
    configured["backup_restic"]["repository_id"] = initialized.initialize()
    atomic_json(directory / "restic-fixture-profile.json", configured)
    return ResticRepository(LocalResticProcess(configured, os.getuid()), clock, budget)
