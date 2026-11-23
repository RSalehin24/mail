"""Refresh the local backup catalog the dashboard lists; runs after every nightly backup.

    python3 -m host.backup_catalog
"""

import json
import sqlite3
import time
from pathlib import Path

from helper.backup_control import BackupControl
from host.backup_operations import BACKUP_LOCK
from host.context import HostContext
from host.daily_retention import RetentionLedger
from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from shared.errors import ServiceError

ROOT = Path("/var/lib/mail-admin-helper")
WORKER_LOCK = "backup-worker.lock"
WORKER_LOCK_WAIT_SECONDS = 1


class BackupCatalog:
    def __init__(
        self, context: HostContext, control: BackupControl, repository: ResticRepository
    ) -> None:
        self.context, self.control, self.repository = context, control, repository

    def refresh(self) -> None:
        now = time.time()
        try:
            with HostLock(BACKUP_LOCK, self.context.coordination.locks.policy):
                snapshots = self.repository.snapshots()
            ledger = RetentionLedger(self.repository).record
            with self.control.connect() as connection:
                pinned = self._pinned(connection)
                connection.execute("DELETE FROM snapshot_details")
                connection.executemany(
                    "INSERT INTO snapshot_details VALUES(?,?)",
                    [
                        (
                            row.identifier,
                            json.dumps(
                                {
                                    "verification": ledger["verification"].get(row.identifier),
                                    "temporary_pin": row.identifier in pinned
                                    or row.identifier
                                    in ledger.get("cleanup", {}).get("temporary_pins", []),
                                    "cleanup": ledger.get("cleanup", {}),
                                }
                            ),
                        )
                        for row in snapshots
                    ],
                )
                connection.execute("DELETE FROM snapshots")
                connection.executemany(
                    "INSERT INTO snapshots VALUES(?,?,?)",
                    [(row.identifier, row.cutoff, now) for row in snapshots],
                )
                connection.execute("INSERT OR REPLACE INTO observations VALUES(1,?,?)", (now, ""))
        except (OSError, ServiceError) as error:
            self._record_failure(now, error)
            raise

    @staticmethod
    def _pinned(connection: sqlite3.Connection) -> set[str]:
        pinned = set()
        for job in connection.execute(
            "SELECT snapshot,result FROM jobs WHERE state IN "
            "('queued','running','reconciliation_required')"
        ):
            pinned.add(job["snapshot"])
            pinned.add(json.loads(job["result"]).get("safety_repository_snapshot", ""))
        return pinned

    def _record_failure(self, now: float, error: OSError | ServiceError) -> None:
        code = error.code if isinstance(error, ServiceError) else "backup_catalog_unavailable"
        with self.control.connect() as connection:
            connection.execute("INSERT OR REPLACE INTO observations VALUES(1,?,?)", (now, code))


def open_catalog(context: HostContext, root: Path = ROOT) -> BackupCatalog:
    repository = ResticRepository(ResticProcess(context.deployment), time.time)
    return BackupCatalog(context, BackupControl(root), repository)


def main() -> None:
    provision_lock(ROOT / WORKER_LOCK)
    context = HostContext.load()
    # The same lock keeps a running restore and this refresh from writing the catalog together.
    with HostLock(ROOT / WORKER_LOCK, LockPolicy(WORKER_LOCK_WAIT_SECONDS)):
        open_catalog(context).refresh()


if __name__ == "__main__":
    main()
