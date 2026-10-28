"""Root service composition from one protected deployment record."""

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helper.journal import Journal
from helper.maddy import DockerMaddy
from host.alerts import Alerts, alert_transport
from host.backup import FULL_STATE_SOURCES, BackupPaths, CaptureDependencies, SnapshotCapture
from host.backup_service import BackupService, backup_service
from host.certificates import (
    CertificatePolicy,
    Consumer,
    GenerationPaths,
    Generations,
    PairValidator,
)
from host.coordination import Coordination, MaintenanceLocks
from host.docker import Docker, DockerConsumer, DockerSnapshots
from host.locks import LockPolicy
from host.renewal import Certbot, Renewal
from shared.files import protected_json


@dataclass(frozen=True)
class HostContext:
    deployment: dict[str, Any]
    docker: Docker
    generations: Generations
    coordination: Coordination
    alerts: Alerts

    @classmethod
    def load(cls) -> "HostContext":
        deployment = protected_json(Path("/etc/mail-admin/deployment.json"))
        inputs = deployment["inputs"]
        generations = Generations(
            GenerationPaths(
                Path("/opt/maddy/tls/generations"),
                Path("/var/lib/mail-admin-certificates/state.json"),
            ),
            (CertificatePolicy(inputs["service_gid"]), PairValidator(), time.time),
        )
        locks = MaintenanceLocks(
            Path("/run/lock/mail-admin-renewal.lock"),
            Path("/run/lock/mail-admin-maddy.lock"),
            LockPolicy(inputs["lock_wait_seconds"]),
        )
        coordination = Coordination(
            locks, (Journal(Path("/var/lib/mail-admin-helper/journal.db")), generations)
        )
        alerts = Alerts(
            Path("/var/lib/mail-admin-alerts/state.json"),
            (alert_transport(deployment["alert_configuration"]), time.time),
        )
        return cls(
            deployment,
            Docker(timeout=inputs["operation_timeout_seconds"]),
            generations,
            coordination,
            alerts,
        )

    def renewal(self) -> Renewal:
        consumers: dict[str, Consumer] = {
            name: DockerConsumer(record, self.docker)
            for name, record in self.deployment["consumers"].items()
        }
        certbot = Certbot(
            Docker(timeout=self.deployment["renewal_timeout_seconds"]),
            (self.generations, self.deployment),
        )
        return Renewal(self.coordination, (certbot, consumers))

    def backup(self) -> BackupService:
        sources = {name: Path(path) for name, path in self.deployment["backup_sources"].items()}
        if not FULL_STATE_SOURCES <= set(sources):
            from shared.errors import ServiceError

            raise ServiceError("backup_sources_incomplete", 503)
        paths = BackupPaths(
            Path("/var/lib/mail-admin-backup/staging"),
            Path("/var/lib/mail-admin-backup/manifest.json"),
            sources,
            tuple(self.deployment["maddy_message_paths"]),
        )
        services = DockerSnapshots(
            (Docker(timeout=30), DockerMaddy(self.deployment), self.generations),
            startup_timeout=30,
        )
        capture = SnapshotCapture(
            paths, CaptureDependencies(self.coordination, services, time.time),
        )
        return backup_service(capture, self.deployment, self.docker)
