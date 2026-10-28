"""Explicit operator-requested one-time repository cleanup with an audited retirement record."""

import shutil
from pathlib import Path
from typing import Any

from host.backup_operations import BACKUP_LOCK
from host.context import HostContext
from host.daily_retention import RetentionLedger
from host.locks import HostLock, LockPolicy
from host.repository_backup import RepositoryBackup
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


def initial_cleanup(context: HostContext) -> dict[str, Any]:
    backup = context.backup()
    if not isinstance(backup, RepositoryBackup):
        raise ServiceError("restic_repository_required", 409)
    repository = backup.repository
    with HostLock(Path("/var/lib/mail-admin-helper/backup-worker.lock"), LockPolicy(1)):
        with HostLock(BACKUP_LOCK, LockPolicy(1)):
            with HostLock(Path("/var/lib/mail-admin-helper/recovery-worker.lock"), LockPolicy(1)):
                from helper.backup_control import BackupControl
                from helper.recovery_records import RecoveryRecords

                if (
                    BackupControl(Path("/var/lib/mail-admin-helper")).busy()
                    or RecoveryRecords(Path("/var/lib/mail-admin-helper/recovery.db")).busy()
                ):
                    raise ServiceError("recovery_operation_in_progress", 409)
                rows = sorted(repository.snapshots(), key=lambda row: row.cutoff, reverse=True)
                if not rows:
                    raise ServiceError("backup_unavailable", 409)
                newest = rows[0]
                target = backup.paths.staging / "initial-cleanup-verification"
                if target.exists():
                    raise ServiceError("restore_target_exists", 409)
                try:
                    repository.restore(repository.reference(newest.identifier), target)
                finally:
                    if target.exists():
                        shutil.rmtree(target)
                ledger = RetentionLedger(repository)
                ledger.verified(newest.identifier)
                retired_path = ledger.path.parent / "retired-references.json"
                previous = protected_json(retired_path) if retired_path.exists() else {}
                atomic_json(
                    retired_path,
                    {
                        "schema_version": 1,
                        "reason": "operator_requested_initial_cleanup",
                        "created": repository.clock(),
                        "verified_retained": newest.identifier,
                        "retired_staging_ids": sorted(
                            set(previous.get("retired_staging_ids", []))
                            | {row.path.name for row in rows[1:]}
                        ),
                        "retired_repository_ids": sorted(
                            set(previous.get("retired_repository_ids", []))
                            | {row.identifier for row in rows[1:]}
                        ),
                    },
                )
                ledger.reconcile({newest.identifier}, initial=True)
                remaining = repository.snapshots()
                if len(remaining) != 1 or remaining[0].identifier != newest.identifier:
                    raise ServiceError("initial_cleanup_verification_failed", 503)
                return {"retained": newest.identifier, "verified": True, "count": 1}


if __name__ == "__main__":
    import json

    print(json.dumps(initial_cleanup(HostContext.load())))
