"""Record scheduled backup outcomes in the same durable catalog as manual operations."""

import time
import uuid
from pathlib import Path

from helper.backup_control import BackupControl
from host.backup_operations import BackupOperations
from host.context import HostContext
from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock


def tracked_backup(context: HostContext) -> None:
    root = Path("/var/lib/mail-admin-helper")
    provision_lock(root / "backup-worker.lock")
    with HostLock(root / "backup-worker.lock", LockPolicy(1)):
        control = BackupControl(root)
        identifier = str(uuid.uuid4())
        control.handle(
            "backup_submit",
            identifier,
            {
                "kind": "backup",
                "snapshot": "",
                "previous": "",
                "confirmation": "",
                "acknowledged": True,
                "password_hashes": {},
            },
            time.time(),
        )
        control.update(identifier, "running", "backup_capture", time.time())
        try:
            manifest = BackupOperations(context.backup(), context.deployment).run()
            control.update(
                identifier,
                "complete",
                "complete",
                time.time(),
                result={
                    "verified": True,
                    "snapshot_id": manifest["snapshot_id"],
                    "cleanup": manifest.get("cleanup", {}),
                },
            )
        except BaseException:
            control.update(
                identifier,
                "reconciliation_required",
                "backup_capture",
                time.time(),
                "scheduled_backup_failed_requires_check",
            )
            raise
