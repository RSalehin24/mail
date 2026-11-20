"""Construction of the helper's dependencies, shared by the root helper and in-process hosts."""

import time
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from helper.backup_control import BackupControl
from helper.deletion import DeletionGuard, DeletionPaths
from helper.forwarding import ForwardingControl
from helper.journal import Journal
from helper.maddy import MaddyAccess
from helper.recovery import RecoveryControl
from helper.recovery_records import RecoveryRecords
from helper.service import HelperDependencies
from helper.status import HostStatusPaths, host_status
from helper.submission import RelayActions, SubmissionControl
from host.locks import HostLock, LockPolicy

DEPLOYMENT_RECORD = Path("/etc/mail-admin/deployment.json")


@dataclass(frozen=True)
class HostLocations:
    """Where the helper keeps its state; the defaults are the root helper's host paths."""

    state: Path = Path("/var/lib/mail-admin-helper")
    lock: Path = Path("/run/lock/mail-admin-maddy.lock")
    owner: int = 0
    relay: RelayActions | None = None  # None: the root helper's Docker verify and restart
    deletion: DeletionPaths = field(default_factory=lambda: DeletionPaths(
        DEPLOYMENT_RECORD, Path("/etc/mail-admin/helper-policy.json"),
        Path("/var/lib/mail-admin-backup/manifest.json"),
    ))
    status: HostStatusPaths = field(default_factory=HostStatusPaths)


def build_dependencies(
    deployment: dict[str, Any], maddy: MaddyAccess, locations: HostLocations | None = None
) -> HelperDependencies:
    locations = locations or HostLocations()
    inputs, state = deployment["inputs"], locations.state
    data = Path(deployment["maddy_data_directory"])
    dependencies = HelperDependencies(
        maddy,
        Journal(state / "journal.db", locations.owner),
        DeletionGuard(locations.deletion, time.time),
        locations.lock,
        LockPolicy(inputs["lock_wait_seconds"], locations.owner),
        time.time,
        partial(host_status, locations.status),
        RecoveryControl(state, RecoveryRecords(state / "recovery.db"),
                        policy_owner=locations.owner)
        if (state / "recovery-policy.json").is_file()
        else None,
        BackupControl(state),
        # Root workers write the Cloudflare records the forwarding page reads.
        ForwardingControl(state, data, worker_owner=0),
        read_lock_policy=LockPolicy(max(
            inputs["lock_wait_seconds"], inputs["operation_timeout_seconds"] * 0.75,
        ), locations.owner),
    )
    if (data / "submission-relay.conf").is_file():
        with HostLock(dependencies.lock, dependencies.lock_policy):
            dependencies = replace(dependencies, submission=SubmissionControl(
                data, Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db",
                locations.relay))
    return dependencies
