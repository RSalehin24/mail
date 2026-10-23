"""Pinned Restic execution, private credentials and durable unknown-completion guards."""

import hashlib
import json
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helper.process import Command, execute
from host.locks import HostLock, LockPolicy
from host.r2 import R2Options, r2_credentials
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json

MAX_COMPLETED_COMMANDS = 64
RESTIC_MEMORY_TARGET_MIB = 96


def resource_environment() -> dict[str, str]:
    # Leave headroom for the Python coordinator and key-calibration allocation spikes.
    return {"GOMAXPROCS": "1", "GOMEMLIMIT": f"{RESTIC_MEMORY_TARGET_MIB}MiB", "TZ": "UTC"}


def require_no_active_commands(caches: set[str]) -> None:
    proc = Path("/proc")
    if not proc.is_dir():
        raise ServiceError("linux_process_observation_required", 503)
    for path in proc.glob("[0-9]*/cmdline"):
        try:
            with path.open("rb") as stream:
                arguments = stream.read(65537)
        except FileNotFoundError:
            continue
        if len(arguments) > 65536:
            raise ServiceError("process_observation_limit_exceeded", 503)
        if any(value.encode() in arguments.split(b"\0") for value in caches):
            raise ServiceError("backup_command_still_running", 409)


@dataclass(frozen=True)
class ResticPaths:
    executable: Path
    password: Path
    state: Path


@dataclass(frozen=True)
class ResticIdentity:
    executable_sha256: str
    repository_id: str | None
    installation_id: str


class ResticProcess:
    def __init__(self, configuration: dict[str, Any], owner: int = 0) -> None:
        self.configuration, self.owner = configuration, owner
        try:
            options = configuration["backup_restic"]
            self.paths = ResticPaths(*(Path(options[key]) for key in (
                "executable", "password_file", "state_directory",
            )))
            self.identity = ResticIdentity(*(options[key] for key in (
                "executable_sha256", "repository_id", "installation_id",
            )))
            self.timeout = options["command_timeout_seconds"]
            self._validate()
        except (KeyError, TypeError, ValueError) as error:
            raise ServiceError("invalid_restic_configuration", 503) from error

    def _validate(self) -> None:
        if type(self.timeout) is not int or not 0 < self.timeout <= 3600:
            raise ServiceError("invalid_restic_deadline", 503)
        identities = [self.identity.executable_sha256]
        if self.identity.repository_id is not None:
            identities.append(self.identity.repository_id)
        for value in identities:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ServiceError("invalid_restic_identity", 503)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.identity.installation_id):
            raise ServiceError("invalid_backup_installation", 503)
        for path in (self.paths.executable, self.paths.password, self.paths.state):
            if not path.is_absolute() or path.is_symlink():
                raise ServiceError("unsafe_restic_path", 503)
        self._validate_executable()
        if not 32 <= len(protected_bytes(self.paths.password, self.owner).strip()) <= 1024:
            raise ServiceError("invalid_restic_password", 503)
        metadata = self.paths.state.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != self.owner
                or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise ServiceError("unsafe_restic_state", 503)

    def _validate_executable(self) -> None:
        metadata = self.paths.executable.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != self.owner
                or stat.S_IMODE(metadata.st_mode) & 0o022 or not metadata.st_mode & 0o100):
            raise ServiceError("unsafe_restic_executable", 503)
        with self.paths.executable.open("rb") as reader:
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
        if digest != self.identity.executable_sha256:
            raise ServiceError("restic_executable_drift", 503)

    def environment(self) -> dict[str, str]:
        credentials = r2_credentials(self.configuration, self.owner)
        options = R2Options.load(self.configuration)
        repository = "s3:https://" + options.host + "/" + options.bucket + "/" + options.prefix
        return {
            "RESTIC_REPOSITORY": repository,
            "RESTIC_PASSWORD_FILE": str(self.paths.password),
            "AWS_ACCESS_KEY_ID": credentials.access_key_id,
            "AWS_SECRET_ACCESS_KEY": credentials.secret_access_key,
            "AWS_DEFAULT_REGION": "auto", **resource_environment(),
        }

    def require_quiet(self) -> None:
        for path in self.paths.state.glob("*.json"):
            record = protected_json(path, self.owner)
            if record.get("state") not in {"exited", "reconciled"}:
                raise ServiceError("backup_command_reconciliation_required", 409)

    def run(self, arguments: tuple[str, ...]) -> str:
        policy = LockPolicy(1, self.owner)
        with HostLock(self.paths.state / "command.lock", policy):
            self.require_quiet()
            self._validate_executable()
            return self._execute(arguments)

    def _execute(self, arguments: tuple[str, ...]) -> str:
        environment = self.environment()
        identifier = str(uuid.uuid4())
        cache = self.paths.state / (identifier + "-cache")
        cache.mkdir(mode=0o700)
        command = (
            str(self.paths.executable), "--json", "--quiet", "--cache-dir", str(cache),
            "--compression", "auto", "--pack-size", "4",
            "-o", "s3.connections=2", "-o", "s3.region=auto",
            "-o", "s3.bucket-lookup=path", "-o", "s3.storage-class=STANDARD",
            "-o", "s3.retries=1",
        ) + arguments
        path = self.paths.state / (identifier + ".json")
        record = {"state": "started", "cache": str(cache),
                  "repository_id": self.identity.repository_id,
                  "installation_id": self.identity.installation_id,
                  "executable_sha256": self.identity.executable_sha256,
                  "arguments_sha256": hashlib.sha256(json.dumps(command).encode()).hexdigest()}
        atomic_json(path, record)
        result = execute(Command(command, timeout=self.timeout, environment=environment))
        atomic_json(path, {**record, "state": "exited", "exit_code": result.code})
        self._remove_cache(cache)
        self._prune_completed()
        if result.code:
            raise ServiceError("restic_exit_" + str(result.code), 503)
        return result.output

    def _prune_completed(self) -> None:
        terminal = [path for path in self.paths.state.glob("*.json")
                    if protected_json(path, self.owner).get("state") in {"exited", "reconciled"}]
        completed = sorted(terminal, key=lambda path: path.stat().st_mtime)
        for path in completed[:-MAX_COMPLETED_COMMANDS]:
            path.unlink()

    def _remove_cache(self, cache: Path) -> None:
        if (cache.parent != self.paths.state or cache.is_symlink()
                or not re.fullmatch(r"[0-9a-f-]{36}-cache", cache.name)):
            raise ServiceError("unsafe_restic_cache", 503)
        if cache.exists():
            shutil.rmtree(cache)

    def reconcile(self) -> None:
        with HostLock(self.paths.state / "command.lock", LockPolicy(1, self.owner)):
            pending = self._pending()
            if not pending:
                return
            require_no_active_commands({record["cache"] for record in pending.values()})
            self._verify_reconciliation_identity()
            self._execute(("unlock",))
            self._execute(("check", "--read-data"))
            for path, record in pending.items():
                self._remove_cache(Path(record["cache"]))
                atomic_json(path, {**record, "state": "reconciled", "exit_code": None,
                                   "outcome": "exit_unknown_repository_checked"})

    def _pending(self) -> dict[Path, dict[str, Any]]:
        records = {path: protected_json(path, self.owner)
                   for path in self.paths.state.glob("*.json")}
        return {path: record for path, record in records.items()
                if record.get("state") not in {"exited", "reconciled"}}

    def _verify_reconciliation_identity(self) -> None:
        expected = self.identity.repository_id
        if expected is None:
            raise ServiceError("known_repository_identity_required", 409)
        try:
            observed = json.loads(self._execute(("cat", "config")))
        except ValueError as error:
            raise ServiceError("invalid_restic_repository", 503) from error
        if not isinstance(observed, dict) or observed.get("id") != expected:
            raise ServiceError("restic_repository_identity_mismatch", 503)
