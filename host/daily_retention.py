"""Durable verified daily restore points; decisions precede native repository mutation."""

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from host.restic_repository import ResticRepository
from zoneinfo import ZoneInfo

from shared.errors import ServiceError
from shared.files import atomic_json, protected_json

DHAKA = ZoneInfo("Asia/Dhaka")


def daily_keep(
    snapshots: list[Any], verified: set[str], pinned: set[str], now: float, window: float
) -> set[str]:
    daily: dict[str, Any] = {}
    keep = set(pinned)
    for row in sorted(snapshots, key=lambda row: (row.cutoff, row.identifier), reverse=True):
        if row.cutoff < now - window:
            continue
        day = datetime.fromtimestamp(row.cutoff, DHAKA).date().isoformat()
        if row.identifier in verified:
            if day not in daily:
                daily[day] = row
                keep.add(row.identifier)
        else:
            # An unverified candidate cannot displace an existing verified restore point.
            keep.add(row.identifier)
    return keep


class RetentionLedger:
    def __init__(self, repository: "ResticRepository") -> None:
        self.repository = repository
        directory = repository.process.paths.state / "retention"
        directory.mkdir(mode=0o700, exist_ok=True)
        if (
            directory.is_symlink()
            or directory.stat().st_uid != repository.process.owner
            or directory.stat().st_mode & 0o077
        ):
            raise ServiceError("unsafe_retention_ledger", 503)
        self.path = directory / "daily-retention.json"
        self.record: dict[str, Any] = (
            protected_json(self.path, repository.process.owner)
            if self.path.exists()
            else {
                "schema_version": 1,
                "verification": {},
                "decisions": [],
                "cleanup": {},
            }
        )

    def save(self) -> None:
        atomic_json(self.path, self.record)

    def verified(self, identifier: str) -> None:
        self.record["verification"][identifier] = {"verified_at": self.repository.clock()}
        self.save()

    def reconcile(self, pinned: set[str], initial: bool = False) -> list[str]:
        rows = self.repository.snapshots()
        now = self.repository.clock()
        keep = daily_keep(
            rows,
            set(self.record["verification"]),
            pinned,
            now,
            self.repository.options.retention_seconds,
        )
        if initial:
            keep = set(pinned)
        expired = sorted(row.identifier for row in rows if row.identifier not in keep)
        decision = {
            "started": now,
            "keep": sorted(keep),
            "remove": expired,
            "temporary_pins": sorted(
                pinned
                - daily_keep(
                    rows,
                    set(self.record["verification"]),
                    set(),
                    now,
                    self.repository.options.retention_seconds,
                )
            ),
            "initial": initial,
            "state": "pending",
        }
        self.record["decisions"].append(decision)
        self.record["cleanup"] = decision
        self.save()
        try:
            removed = self.repository.prune(keep, expired)
        except BaseException:
            decision.update(state="failed", error="retention_cleanup_failed")
            self.save()
            raise
        decision.update(state="complete", completed=self.repository.clock(), error="")
        self.save()
        return removed
