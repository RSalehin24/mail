"""Local root-owned backup catalog and durable operation queue; no network in reads."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.validation import request_id

KINDS = {"backup", "preflight", "restore", "reconcile"}
UNFINISHED = "('queued','running','reconciliation_required')"


def validate_backup(payload: dict[str, Any]) -> dict[str, Any]:
    """The dashboard may only read the backup catalog and log; every change is a VM command."""
    if payload["operation"] != "backup_list" or set(payload) != {
        "operation",
        "protocol",
        "request_id",
        "page",
        "page_size",
        "kind",
    }:
        raise ServiceError("invalid_helper_fields", 400)
    request_id(payload["request_id"])
    if (
        type(payload["page"]) is not int
        or payload["page"] < 1
        or type(payload["page_size"]) is not int
        or payload["page_size"] not in {5, 10, 20, 50}
        or payload["kind"] not in {"snapshots", "operations"}
    ):
        raise ServiceError("invalid_pagination", 400)
    return {k: v for k, v in payload.items() if k not in {"protocol", "operation", "request_id"}}


class BackupControl:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "backup-control.db"
        if self.path.is_symlink() or (
            self.path.exists()
            and (self.path.stat().st_uid != root.stat().st_uid or self.path.stat().st_mode & 0o077)
        ):
            raise ServiceError("unsafe_backup_control", 503)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, snapshot TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL, phase TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL, error TEXT NOT NULL,
                    result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, cutoff REAL NOT NULL, observed REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshot_details (
                    id TEXT PRIMARY KEY, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations (
                    id INTEGER PRIMARY KEY, observed REAL, error TEXT);
                CREATE TABLE IF NOT EXISTS backup_plans (id TEXT PRIMARY KEY, keys TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS backup_imports (
                    id TEXT PRIMARY KEY, completed REAL NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self) -> Any:
        connection = sqlite3.connect(self.path, timeout=0.25)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def job(self, identifier: str, private: bool = False) -> dict[str, Any]:
        request_id(identifier)
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ServiceError("backup_operation_not_found", 404)
        result = dict(row)
        payload = json.loads(result.pop("payload"))
        result["result"] = json.loads(result["result"])
        if private:
            result["payload"] = payload
        return result

    def busy(self) -> bool:
        with self.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM jobs WHERE state IN "
                    "('queued','running','reconciliation_required') LIMIT 1"
                ).fetchone()
                is not None
            )

    def update(
        self,
        identifier: str,
        state: str,
        phase: str,
        now: float,
        error: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET state=?,phase=?,updated=?,error=?,"
                "result=COALESCE(?,result) WHERE id=?",
                (
                    state,
                    phase,
                    now,
                    error,
                    json.dumps(result) if result is not None else None,
                    identifier,
                ),
            )

    def listing(self, values: dict[str, Any]) -> dict[str, Any]:
        table = "snapshots" if values["kind"] == "snapshots" else "jobs"
        order = "cutoff DESC,id" if table == "snapshots" else "created DESC,id"
        with self.connect() as connection:
            total = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 -- fixed tables
            size = values["page_size"]
            page = min(values["page"], max(1, (total + size - 1) // size))
            rows = connection.execute(
                f"SELECT * FROM {table} ORDER BY {order} LIMIT ? OFFSET ?",  # noqa: S608 -- fixed identifiers
                (size, (page - 1) * size),
            ).fetchall()
            observation = connection.execute("SELECT * FROM observations WHERE id=1").fetchone()
        public = [
            dict(row)
            if table == "snapshots"
            else {
                **self.job(row["id"]),
                "result": {"cleanup": self.job(row["id"])["result"].get("cleanup", {})},
            }
            for row in rows
        ]
        if table == "snapshots":
            with self.connect() as connection:
                details = {
                    row[0]: json.loads(row[1])
                    for row in connection.execute("SELECT * FROM snapshot_details")
                }
            public = [{**row, **details.get(row["id"], {})} for row in public]
        return {
            "items": public,
            "total": total,
            "page": page,
            "page_size": size,
            "observation": dict(observation) if observation else None,
            "busy": self.busy(),
        }

    def interrupt_running(self) -> None:
        """Only for a holder of the exclusive worker lock, which proves the owners have exited."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE jobs SET state='reconciliation_required',error='worker_interrupted' "
                "WHERE state='running'"
            )

    def begin(self, record: tuple[str, str, str, str], now: float) -> dict[str, Any]:
        """Record one VM-command run (identifier, kind, snapshot, previous) before it starts."""
        identifier, kind, snapshot, previous = record
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._admit(connection, kind, snapshot, previous)
            connection.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    kind,
                    snapshot,
                    json.dumps({"previous": previous}),
                    "running",
                    "starting",
                    now,
                    now,
                    "",
                    "{}",
                ),
            )
        return self.job(identifier)

    def _admit(
        self, connection: sqlite3.Connection, kind: str, snapshot: str, previous: str
    ) -> None:
        if kind not in KINDS:
            raise ServiceError("invalid_backup_operation", 400)
        blocking = "('queued','running')" if kind == "reconcile" else UNFINISHED
        if connection.execute(f"SELECT 1 FROM jobs WHERE state IN {blocking} LIMIT 1").fetchone():  # noqa: S608 -- fixed states
            raise ServiceError("backup_operation_in_progress", 409)
        if kind in {"preflight", "restore"} and not connection.execute(
            "SELECT 1 FROM snapshots WHERE id=?", (snapshot,)
        ).fetchone():
            raise ServiceError("backup_snapshot_unavailable", 409)
        if kind == "restore":
            prepared = self.job(previous)
            if (
                prepared["kind"] != "preflight"
                or prepared["state"] != "complete"
                or prepared["snapshot"] != snapshot
            ):
                raise ServiceError("backup_preflight_required", 409)
        if kind == "reconcile" and self.job(previous)["state"] != "reconciliation_required":
            raise ServiceError("backup_reconciliation_not_required", 409)
