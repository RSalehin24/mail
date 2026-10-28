"""Encrypted shared data, exact snapshot identities and safe native restoration."""

import json
import re
import shutil
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from host.r2 import R2Options
from host.repository_budget import R2Budget, RepositoryBudget
from host.restic_process import ResticProcess
from host.snapshot_tree import VERIFICATION_FILE, SnapshotTree, snapshot_tree
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json

REFERENCE = re.compile(r"restic:([0-9a-f]{64}):([0-9a-f]{64})")
SNAPSHOT_NAME = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
REPOSITORY_TAG = "mail-admin"


@dataclass(frozen=True)
class ResticSnapshot:
    identifier: str
    path: Path
    cutoff: float


def decode_json(value: str) -> Any:
    try:
        return json.loads(value)
    except ValueError as error:
        raise ServiceError("invalid_restic_response", 503) from error


class ResticRepository:
    def __init__(
        self, process: ResticProcess, clock: Callable[[], float],
        budget: RepositoryBudget | None = None,
    ) -> None:
        self.process, self.clock = process, clock
        self.options = R2Options.load(process.configuration)
        self.budget = budget or R2Budget(process.configuration, process.owner)
        self.staging = Path(process.configuration["backup_restic"]["staging_directory"])
        if not self.staging.is_absolute() or self.staging.is_symlink():
            raise ServiceError("invalid_restic_staging", 503)

    def initialize(self) -> str:
        if self.process.identity.repository_id is not None:
            raise ServiceError("repository_already_configured", 409)
        self._budget(1024 * 1024)
        self.process.run(("init", "--repository-version", "2"))
        return self._identity()

    def _identity(self) -> str:
        record = decode_json(self.process.run(("cat", "config")))
        identifier = record.get("id") if isinstance(record, dict) else None
        if (not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier)
                or record.get("version") != 2):
            raise ServiceError("invalid_restic_repository", 503)
        return identifier

    def verify_identity(self) -> None:
        expected = self.process.identity.repository_id
        if expected is None or self._identity() != expected:
            raise ServiceError("restic_repository_identity_mismatch", 503)

    def _budget(self, incoming_bytes: int) -> None:
        self.budget.require(incoming_bytes)

    def publish(self, snapshot: Path) -> dict[str, Any]:
        self.verify_identity()
        self._source(snapshot)
        tree = snapshot_tree(snapshot)
        if tree.file_bytes > self.options.max_archive_bytes:
            raise ServiceError("r2_snapshot_budget_exceeded", 503)
        self._budget(2 * (tree.file_bytes + tree.entries * 4096) + 4 * 1024 * 1024)
        self._verification_record(snapshot, tree)
        cutoff = protected_json(snapshot / "snapshot.json", self.process.owner)["cutoff"]
        stamp = datetime.fromtimestamp(cutoff, UTC).strftime("%Y-%m-%d %H:%M:%S")
        response = self.process.run((
            "backup", "--host", self.process.identity.installation_id,
            "--tag", REPOSITORY_TAG, "--group-by", "host,tags", "--time", stamp,
            str(snapshot),
        ))
        return self._publication(response, tree)

    def _source(self, snapshot: Path) -> None:
        if (snapshot.parent.resolve() != self.staging.resolve()
                or not SNAPSHOT_NAME.fullmatch(snapshot.name) or snapshot.is_symlink()):
            raise ServiceError("untrusted_restic_source", 503)
        state = self.process.paths.state.resolve()
        if state.is_relative_to(snapshot.resolve()) or snapshot.resolve().is_relative_to(state):
            raise ServiceError("backup_source_overlaps_repository_state", 503)

    def _verification_record(self, snapshot: Path, tree: SnapshotTree) -> None:
        atomic_json(snapshot / VERIFICATION_FILE, {
            "format": "restic-v2", "content_sha256": tree.sha256,
            "file_bytes": tree.file_bytes, "entries": tree.entries,
            "installation_id": self.process.identity.installation_id,
            "repository_id": self.process.identity.repository_id,
        })

    def _publication(self, response: str, tree: SnapshotTree) -> dict[str, Any]:
        records = [decode_json(line) for line in response.splitlines() if line.strip()]
        summaries = [row for row in records
                     if isinstance(row, dict) and row.get("message_type") == "summary"]
        if len(summaries) != 1:
            raise ServiceError("restic_snapshot_completion_unknown", 503)
        summary = summaries[0]
        identifier = summary.get("snapshot_id", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ServiceError("restic_snapshot_completion_unknown", 503)
        return {
            "format": "restic-v2", "reference": self.reference(identifier),
            "content_sha256": tree.sha256,
            "data_added_bytes": summary.get("data_added_packed"),
            "repository_snapshot": identifier,
        }

    def reference(self, identifier: str) -> str:
        return "restic:" + str(self.process.identity.repository_id) + ":" + identifier

    def snapshots(self) -> list[ResticSnapshot]:
        self.verify_identity()
        records = decode_json(self.process.run((
            "snapshots", "--host", self.process.identity.installation_id, "--tag", REPOSITORY_TAG,
        )))
        if not isinstance(records, list):
            raise ServiceError("invalid_restic_snapshots", 503)
        return [self._snapshot(row) for row in records]

    def _snapshot(self, record: dict[str, Any]) -> ResticSnapshot:
        try:
            identifier, paths = record["id"], record["paths"]
            cutoff = datetime.fromisoformat(record["time"].replace("Z", "+00:00"))
            if (not re.fullmatch(r"[0-9a-f]{64}", identifier) or len(paths) != 1
                    or record["hostname"] != self.process.identity.installation_id
                    or REPOSITORY_TAG not in record["tags"] or cutoff.tzinfo is None):
                raise ValueError("Invalid snapshot metadata")
            path = Path(paths[0])
            if path.parent != self.staging or not SNAPSHOT_NAME.fullmatch(path.name):
                raise ValueError("Invalid snapshot path")
            return ResticSnapshot(identifier, path, cutoff.timestamp())
        except (KeyError, ValueError, TypeError, AttributeError) as error:
            raise ServiceError("invalid_restic_snapshot", 503) from error

    def restore(self, reference: str, destination: Path) -> dict[str, Any]:
        match = REFERENCE.fullmatch(reference)
        if match is None or match[1] != self.process.identity.repository_id:
            raise ServiceError("untrusted_archive_reference", 503)
        choices = [row for row in self.snapshots() if row.identifier == match[2]]
        if len(choices) != 1:
            raise ServiceError("restic_snapshot_unavailable", 503)
        if destination.exists() or destination.is_symlink():
            raise ServiceError("restore_target_exists", 409)
        parent = destination.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != self.process.owner
                or stat.S_IMODE(parent.st_mode) & 0o022 or ".." in destination.parts):
            raise ServiceError("unsafe_restore_parent", 503)
        destination.mkdir(mode=0o700)
        self.process.run((
            "restore", match[2] + ":" + str(choices[0].path),
            "--target", str(destination), "--overwrite", "never", "--verify",
        ))
        return self.verify_restored(destination)

    def verify_restored(self, destination: Path) -> dict[str, Any]:
        record = protected_json(destination / VERIFICATION_FILE, self.process.owner)
        if (record.get("format") != "restic-v2"
                or record.get("repository_id") != self.process.identity.repository_id
                or record.get("installation_id") != self.process.identity.installation_id
                or snapshot_tree(destination).sha256 != record.get("content_sha256")):
            raise ServiceError("restored_snapshot_integrity_failed", 503)
        return record

    def verify(self, publication: dict[str, Any]) -> None:
        target = self.staging / ("verification-" + str(uuid.uuid4()))
        try:
            restored = self.restore(publication["reference"], target)
            if restored["content_sha256"] != publication["content_sha256"]:
                raise ServiceError("restored_snapshot_integrity_failed", 503)
        finally:
            if target.exists():
                shutil.rmtree(target)

    def prune(self, protected: set[str], selected: list[str] | None = None) -> list[str]:
        snapshots = self.snapshots()
        known = {row.identifier for row in snapshots}
        if not protected or not protected <= known:
            raise ServiceError("backup_retention_protection_required", 503)
        if any(row.cutoff > self.clock() for row in snapshots):
            raise ServiceError("backup_clock_precedes_snapshot", 503)
        self.process.run(("check", "--read-data"))
        expired = [row.identifier for row in snapshots if row.identifier not in protected
                   and row.cutoff <= self.clock() - self.options.retention_seconds]
        if selected is not None:
            if not set(selected) <= known - protected:
                raise ServiceError("invalid_retention_selection", 503)
            expired = selected
        self._budget(8 * 1024 * 1024)
        if expired:
            self.process.run(("forget", *expired))
        self.process.run(("prune", "--max-repack-size", "8M", "--max-unused", "0"))
        self.process.run(("check", "--read-data"))
        return expired
