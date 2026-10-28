"""First baseline capture precedes the helper launcher and dashboard installation."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from helper.journal import Journal
from helper.maddy import DockerMaddy
from helper.process import Command, CommandResult, execute
from host.backup import FULL_STATE_SOURCES, BackupPaths, CaptureDependencies, SnapshotCapture
from host.backup_service import BackupService, backup_service
from host.certificates import verify_served
from host.coordination import MaintenanceLocks
from host.docker import Docker
from host.legacy import LegacyRenewal
from host.locks import HostLock, LockPolicy
from host.rollout import rollout_directory
from shared.errors import ServiceError


class ExistingMaddy(DockerMaddy):
    def expected_cli_version(self) -> str:
        configuration = {
            **self.deployment,
            'maddy_cli_version': self.deployment.get(
                'baseline_maddy_cli_version', self.deployment.get('maddy_cli_version', '0.9.5'),
            ),
        }
        return DockerMaddy(configuration, self.executable).expected_cli_version()

    def expected_mounts(self) -> dict[str, str]:
        return dict(self.deployment["maddy_mounts"])

    def change(self, arguments: tuple[str, ...], password: str = "") -> CommandResult:
        if arguments not in {("version",), ("creds", "list"), ("imap-acct", "list")} or password:
            raise ServiceError("baseline_read_only", 403)
        # Installing the launcher before this capture would violate the baseline gate.
        return execute(
            Command(
                (self.executable, "exec", "-i", "maddy", "/bin/maddy") + arguments,
                timeout=self.timeout,
            )
        )


class BaselineScope:
    def __init__(
        self, locks: MaintenanceLocks, dependencies: tuple[LegacyRenewal, Journal | None]
    ) -> None:
        self.locks = locks
        self.legacy, self.journal = dependencies

    @contextmanager
    def consistent(self) -> Iterator[None]:
        self.legacy.verify()
        with HostLock(self.locks.renewal, self.locks.policy):
            with HostLock(self.locks.maddy, self.locks.policy):
                self.legacy.verify()
                if self.journal is not None and self.journal.unresolved():
                    raise ServiceError("helper_reconciliation_required", 409)
                yield


class BaselineServices:
    def __init__(
        self, docker: Docker, deployment: dict[str, Any], source_names: set[str]
    ) -> None:
        self.docker = docker
        self.deployment = deployment
        self.source_names = source_names
        configuration = {**deployment, "maddy_mounts": deployment["baseline_maddy_mounts"]}
        if 'baseline_maddy_image_id' in deployment:
            configuration['inputs'] = {
                **deployment['inputs'], 'maddy_image_id': deployment['baseline_maddy_image_id'],
            }
        self.maddy = ExistingMaddy(configuration, docker.executable)

    def inventories(self) -> dict[str, list[str]]:
        self.require_existing_dashboard_sources()
        self.maddy.validate()
        return self.maddy.inventories()

    def require_existing_dashboard_sources(self) -> None:
        deployed = self.docker.run(
            ("ps", "--all", "--filter", "name=^/mail-admin$", "--format", "{{.Names}}")
        )
        if deployed not in {"", "mail-admin"}:
            raise ServiceError("baseline_dashboard_identity_unknown", 503)
        if deployed and not FULL_STATE_SOURCES <= self.source_names:
            raise ServiceError("baseline_dashboard_sources_incomplete", 503)

    def running(self) -> list[str]:
        deployed = self.docker.run(("ps", "--format", "{{.Names}}"))
        return [name for name in deployed.splitlines() if name in {"maddy", "mail-admin"}]

    def pause(self, names: list[str]) -> None:
        for name in names:
            self.docker.run(("stop", "--time", "30", name))
            if self.docker.inspect(name)["State"]["Running"]:
                raise ServiceError("baseline_stop_incomplete", 503)

    def resume(self, names: list[str]) -> None:
        failures = []
        for name in names:
            try:
                self.docker.run(("start", name))
                if not self.docker.inspect(name)["State"]["Running"]:
                    failures.append(name)
                expected = self.deployment["baseline_served_certificates"][name]
                verify_served(
                    ("127.0.0.1", expected["port"]), expected["sha256"], self.docker.timeout
                )
            except (OSError, ServiceError):
                failures.append(name)
        if failures:
            raise ServiceError("baseline_service_recovery_failed", 503)


def baseline_backup(deployment: dict[str, Any]) -> BackupService:
    sources = {name: Path(path) for name, path in deployment["baseline_backup_sources"].items()}
    required = {
        "maddy",
        "configuration",
        "compose",
        "tls",
        "acme",
        "acme_lib",
        "renewal_script",
        "renewal_units",
        "image_identity",
    }
    if not required <= set(sources):
        raise ServiceError("baseline_sources_incomplete", 503)
    docker = Docker(timeout=deployment["inputs"]["operation_timeout_seconds"])
    locks = MaintenanceLocks(
        Path("/run/lock/mail-admin-renewal.lock"),
        Path("/run/lock/mail-admin-maddy.lock"),
        LockPolicy(deployment["inputs"]["lock_wait_seconds"]),
    )
    journal_path = Path("/var/lib/mail-admin-helper/journal.db")
    scope = BaselineScope(
        locks,
        (
            LegacyRenewal(rollout_directory(deployment) / "legacy.json", 30),
            Journal(journal_path) if journal_path.exists() else None,
        ),
    )
    paths = BackupPaths(
        Path("/var/lib/mail-admin-backup/staging"),
        rollout_directory(deployment) / "baseline-manifest.json",
        sources,
        tuple(deployment["maddy_message_paths"]),
    )
    capture = SnapshotCapture(
        paths,
        CaptureDependencies(scope, BaselineServices(docker, deployment, set(sources)), time.time),
    )
    return backup_service(capture, deployment, docker)
