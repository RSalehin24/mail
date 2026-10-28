"""Restricted provider synchronization with durable rollback before restarting Maddy."""

import hashlib
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from helper.process import Command, execute
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json, sync_directory
from shared.submission import relay_configuration


def write_configuration(path: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".submission-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


class SubmissionControl:
    def __init__(self, data: Path, database: Path | None = None) -> None:
        self.path = data / "submission-relay.conf"
        self.rollback = data / "submission-relay.rollback"
        self.pending = data / "submission-relay.pending.json"
        self.database = database
        self.reconcile()

    def reconcile(self) -> None:
        if not self.rollback.exists():
            if self.pending.exists():
                self.pending.unlink()
                sync_directory(self.path.parent)
            return
        committed = False
        if self.pending.exists() and self.database is not None:
            pending = protected_json(self.pending)
            with sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True,
                                 timeout=0.25) as connection:
                row = connection.execute(
                    "SELECT revision FROM relay_settings WHERE id=1").fetchone()
            committed = bool(row and row[0] == pending["revision"] and
                             hashlib.sha256(protected_bytes(self.path)).hexdigest()
                             == pending["sha256"])
        if not committed:
            write_configuration(self.path, protected_bytes(self.rollback))
            self.restart()
        self.rollback.unlink()
        sync_directory(self.path.parent)
        self.pending.unlink(missing_ok=True)
        sync_directory(self.path.parent)

    def restart(self) -> None:
        result = execute(Command(("/usr/bin/docker", "restart", "--time", "1", "maddy"),
                                 timeout=15))
        if result.code:
            raise ServiceError("submission_restart_failed", 503)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = execute(Command(("/usr/bin/docker", "exec", "maddy", "/bin/maddy",
                                      "-config", "/data/maddy.conf", "verify-config"), timeout=3))
            if result.code == 0 and "app.Run failed" not in result.output:
                return
            time.sleep(0.1)
        raise ServiceError("submission_restart_failed", 503)

    def synchronize(self, values: dict[str, Any]) -> dict[str, Any]:
        self.reconcile()
        revision = values["revision"]
        candidate = relay_configuration({key: values[key]
                                         for key in ("username", "credential")}).encode()
        previous = protected_bytes(self.path)
        if candidate == previous:
            return {"synchronized": True}
        write_configuration(self.rollback, previous)
        atomic_json(self.pending, {"revision": revision,
                                  "sha256": hashlib.sha256(candidate).hexdigest()})
        try:
            write_configuration(self.path, candidate)
            result = execute(Command(("/usr/bin/docker", "exec", "maddy", "/bin/maddy",
                                      "-config", "/data/maddy.conf", "verify-config"), timeout=3))
            if result.code or "app.Run failed" in result.output:
                raise ServiceError("submission_configuration_invalid", 503)
            self.restart()
        except (OSError, ServiceError) as error:
            write_configuration(self.path, previous)
            try:
                self.restart()
            except (OSError, ServiceError) as rollback_error:
                raise ServiceError("submission_rollback_failed", 503) from rollback_error
            self.pending.unlink(missing_ok=True)
            self.rollback.unlink()
            sync_directory(self.path.parent)
            raise ServiceError("submission_synchronization_failed", 503) from error
        return {"synchronized": True}
