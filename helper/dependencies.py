"""Construction of the helper's dependencies, shared by the root helper and in-process hosts."""

import time
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

from helper.backup_control import BackupControl
from helper.deletion import DeletionGuard, DeletionPaths
from helper.direct_deletion import DeletionStorage, DirectDeletion
from helper.forwarding import ForwardingControl
from helper.journal import Journal
from helper.maddy import MaddyAccess
from helper.recovery import RecoveryControl
from helper.recovery_records import RecoveryRecords
from helper.service import HelperDependencies
from helper.state_database import STATE_FILE
from helper.status import HostStatusPaths, host_status
from helper.submission import RelayActions, SubmissionControl
from host.locks import HostLock, LockPolicy
from host.mailbox_archive import MailboxArchives

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
    direct_deletion: bool = False  # The dashboard deletes mailboxes itself, in one request


def recovery_control(
    deployment: dict[str, Any], services: tuple[MaddyAccess, Journal], locations: HostLocations
) -> RecoveryControl:
    state, records = locations.state, RecoveryRecords(locations.state / STATE_FILE)
    deletion = None
    if locations.direct_deletion:
        storage = DeletionStorage(
            Path(deployment["maddy_data_directory"]),
            Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db",
        )
        deletion = DirectDeletion(storage, (*services, records, lambda: MailboxArchives(
            state / "recovery-points", state / "recovery.key", locations.owner)))
    # With direct deletion the dashboard user owns the archives and holds the key.
    owner = locations.owner if deletion else 0
    return RecoveryControl(state, records, owner, locations.owner, deletion)


def build_dependencies(
    deployment: dict[str, Any], maddy: MaddyAccess, locations: HostLocations | None = None
) -> HelperDependencies:
    locations = locations or HostLocations()
    inputs, state = deployment["inputs"], locations.state
    data = Path(deployment["maddy_data_directory"])
    journal = Journal(state / STATE_FILE, locations.owner)
    dependencies = HelperDependencies(
        maddy,
        journal,
        DeletionGuard(locations.deletion, time.time),
        locations.lock,
        LockPolicy(inputs["lock_wait_seconds"], locations.owner),
        time.time,
        partial(host_status, locations.status),
        recovery_control(deployment, (maddy, journal), locations)
        if (state / "recovery-policy.json").is_file()
        else None,
        BackupControl(state),
        # Root workers write the Cloudflare records the forwarding page reads.
        ForwardingControl(state, data),
        read_lock_policy=LockPolicy(max(
            inputs["lock_wait_seconds"], inputs["operation_timeout_seconds"] * 0.75,
        ), locations.owner),
    )
    if (data / "submission-relay.conf").is_file():
        database = Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db"
        if interrupted_relay_change(data):
            # Finishing the change races with the workers, so it needs the mailbox lock.
            with HostLock(dependencies.lock, dependencies.lock_policy):
                submission = SubmissionControl(data, database, locations.relay)
        else:
            # Starting must never wait for a backup that is waiting for this process to start.
            submission = SubmissionControl(data, database, locations.relay)
        dependencies = replace(dependencies, submission=submission)
    return dependencies


def interrupted_relay_change(data: Path) -> bool:
    return any(
        (data / name).exists()
        for name in ("submission-relay.rollback", "submission-relay.pending.json")
    )
