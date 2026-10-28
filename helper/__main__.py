"""Root-only helper service, using root-controlled operator records."""

import os
import time
from dataclasses import replace
from pathlib import Path

from helper.backup_control import BackupControl
from helper.deletion import DeletionGuard, DeletionPaths
from helper.forwarding import ForwardingControl
from helper.journal import Journal
from helper.maddy import DockerMaddy
from helper.recovery import RecoveryControl
from helper.recovery_records import RecoveryRecords
from helper.server import HelperServer, SocketPolicy
from helper.service import HelperDependencies, HostHelper
from helper.status import host_status
from helper.submission import SubmissionControl
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import protected_json
from shared.validation import HELPER_PROTOCOL, SCHEMA_VERSION


def main() -> None:
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    deployment = protected_json(Path("/etc/mail-admin/deployment.json"))
    if (
        deployment.get("helper_protocol") != HELPER_PROTOCOL
        or deployment.get("schema_version") != SCHEMA_VERSION
    ):
        raise ServiceError("incompatible_deployment", 503)
    inputs = deployment["inputs"]
    paths = DeletionPaths(
        Path("/etc/mail-admin/deployment.json"),
        Path("/etc/mail-admin/helper-policy.json"),
        Path("/var/lib/mail-admin-backup/manifest.json"),
    )
    dependencies = HelperDependencies(
        DockerMaddy(deployment),
        Journal(Path("/var/lib/mail-admin-helper/journal.db")),
        DeletionGuard(paths, time.time),
        Path("/run/lock/mail-admin-maddy.lock"),
        LockPolicy(inputs["lock_wait_seconds"]),
        time.time,
        host_status,
        RecoveryControl(
            Path("/var/lib/mail-admin-helper"),
            RecoveryRecords(Path("/var/lib/mail-admin-helper/recovery.db")),
        )
        if Path("/var/lib/mail-admin-helper/recovery-policy.json").is_file()
        else None,
        BackupControl(Path("/var/lib/mail-admin-helper")),
        ForwardingControl(
            Path("/var/lib/mail-admin-helper"), Path(deployment["maddy_data_directory"])
        ),
    )
    if (Path(deployment["maddy_data_directory"]) / "submission-relay.conf").is_file():
        with HostLock(dependencies.lock, dependencies.lock_policy):
            dependencies = replace(dependencies, submission=SubmissionControl(
                Path(deployment["maddy_data_directory"]),
                Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db"))
    policy = SocketPolicy(
        inputs["service_uid"], inputs["service_gid"], inputs["operation_timeout_seconds"]
    )
    with HelperServer(
        Path("/run/mail-admin-helper/control.sock"), (HostHelper(dependencies), policy)
    ) as server:
        server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
