"""Select the requested repository without changing consistent-capture behavior."""

from typing import Any

from host.backup import Backup, BackupDependencies, SnapshotCapture
from host.docker import Docker
from host.image_tools import ImageTools
from host.repository_backup import RepositoryBackup
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from host.transfer import archive_transfer

BackupService = Backup | RepositoryBackup


def backup_service(
    capture: SnapshotCapture, deployment: dict[str, Any], docker: Docker,
) -> BackupService:
    dependencies = capture.dependencies
    if deployment["inputs"]["backup_destination"].startswith("s3://"):
        repository = ResticRepository(ResticProcess(deployment), dependencies.clock)
        return RepositoryBackup(capture, repository)
    return Backup(capture.paths, BackupDependencies(
        dependencies.coordination, dependencies.services,
        ImageTools(docker, deployment), archive_transfer(deployment), dependencies.clock,
    ))
