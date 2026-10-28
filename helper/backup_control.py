"""Local root-owned backup catalog and durable operation queue; no network in reads."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.validation import address, request_id

KINDS = {"refresh", "backup", "preflight", "restore", "reconcile"}


def validate_backup(payload: dict[str, Any]) -> dict[str, Any]:
    operation = payload["operation"]
    fields = {
        "backup_list": {"page", "page_size", "kind"},
        "backup_outcome": {"job_id"},
        "backup_submit": {
            "kind",
            "snapshot",
            "previous",
            "confirmation",
            "acknowledged",
            "password_hashes",
        },
    }
    if operation not in fields or set(payload) != fields[operation] | {
        "operation",
        "protocol",
        "request_id",
    }:
        raise ServiceError("invalid_helper_fields", 400)
    request_id(payload["request_id"])
    if operation == "backup_list":
        if (
            type(payload["page"]) is not int
            or payload["page"] < 1
            or type(payload["page_size"]) is not int
            or payload["page_size"] not in {5, 10, 20, 50}
            or payload["kind"] not in {"snapshots", "operations"}
        ):
            raise ServiceError("invalid_pagination", 400)
    elif operation == "backup_outcome":
        request_id(payload["job_id"])
    else:
        if payload["kind"] not in KINDS or payload["acknowledged"] is not True:
            raise ServiceError("backup_confirmation_required", 400)
        if not isinstance(payload["snapshot"], str) or (
            payload["snapshot"] and not re.fullmatch(r"[0-9a-f]{64}", payload["snapshot"])
        ):
            raise ServiceError("exact_snapshot_id_required", 400)
        if payload["previous"]:
            request_id(payload["previous"])
        if (
            payload["kind"] in {"preflight", "restore"}
            and payload["confirmation"] != payload["snapshot"]
        ):
            raise ServiceError("backup_confirmation_required", 400)
        if not isinstance(payload["password_hashes"], dict) or len(payload["password_hashes"]) > 32:
            raise ServiceError("invalid_mailbox_passwords", 400)
        for email, hashed in payload["password_hashes"].items():
            address(email)
            if not isinstance(hashed, str) or not re.fullmatch(
                r"argon2:3:19456:1:[A-Za-z0-9+/]{22}==:[A-Za-z0-9+/]{86}==", hashed
            ):
                raise ServiceError("invalid_mailbox_passwords", 400)
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

    def handle(
        self, operation: str, identifier: str, values: dict[str, Any], now: float
    ) -> dict[str, Any]:
        if operation == "backup_outcome":
            return self.job(values["job_id"])
        if operation == "backup_list":
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
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
            encoded = json.dumps(values, sort_keys=True)
            if previous is not None:
                if previous["payload"] != encoded:
                    raise ServiceError("request_id_conflict", 409)
            else:
                if (
                    values["kind"] == "reconcile"
                    and connection.execute(
                        "SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1"
                    ).fetchone()
                ):
                    raise ServiceError("backup_operation_in_progress", 409)
                if (
                    connection.execute(
                        "SELECT 1 FROM jobs WHERE state IN "
                        "('queued','running','reconciliation_required') LIMIT 1"
                    ).fetchone()
                    and values["kind"] != "reconcile"
                ):
                    raise ServiceError("backup_operation_in_progress", 409)
                if values["previous"]:
                    original = self.job(values["previous"])
                    if values["kind"] == "backup" and (
                        original["kind"] != "backup" or original["state"] != "failed"
                    ):
                        raise ServiceError("backup_not_retryable", 409)
                if (
                    values["kind"] in {"preflight", "restore"}
                    and not connection.execute(
                        "SELECT 1 FROM snapshots WHERE id=?", (values["snapshot"],)
                    ).fetchone()
                ):
                    raise ServiceError("backup_snapshot_unavailable", 409)
                if values["kind"] == "restore":
                    prepared = self.job(values["previous"])
                    if (
                        prepared["kind"] != "preflight"
                        or prepared["state"] != "complete"
                        or prepared["snapshot"] != values["snapshot"]
                    ):
                        raise ServiceError("backup_preflight_required", 409)
                    if set(values["password_hashes"]) != set(prepared["result"]["missing"]):
                        raise ServiceError("missing_mailbox_passwords_required", 400)
                if values["kind"] == "reconcile":
                    original = self.job(values["previous"])
                    if original["state"] != "reconciliation_required":
                        raise ServiceError("backup_reconciliation_not_required", 409)
                connection.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        values["kind"],
                        values["snapshot"],
                        encoded,
                        "queued",
                        "queued",
                        now,
                        now,
                        "",
                        "{}",
                    ),
                )
        return self.job(identifier)
