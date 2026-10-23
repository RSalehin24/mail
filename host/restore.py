"""Stage a verified recovery point without replacing live data or starting services."""

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import quote

from host.backup import FULL_STATE_SOURCES
from host.image_tools import ImageTools, require_private_terminal
from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock
from host.restic_repository import ResticRepository
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy


class Restore:
    def __init__(self, repository: ResticRepository) -> None:
        self.repository = repository

    def stage(self, reference: str, destination: Path) -> dict[str, Any]:
        verified = self.repository.restore(reference, destination)
        roles = {path.name for path in destination.iterdir()}
        missing = FULL_STATE_SOURCES - roles
        self._databases(destination)
        result = {
            "state": "verified_staging", "reference": reference,
            "content_sha256": verified["content_sha256"],
            "full_state_sources_present": not missing,
            "missing_full_state_sources": sorted(missing),
            "destination": str(destination), "services_started": False,
            "authentication_prepared": False,
        }
        atomic_json(destination.with_name(destination.name + ".restore.json"), result)
        provision_lock(destination.with_name(destination.name + ".restore.lock"),
                       self.repository.process.owner)
        if {"dashboard", "operator"} & roles:
            self.close_authentication(destination)
        return protected_json(destination.with_name(destination.name + ".restore.json"),
                              self.repository.process.owner)

    @staticmethod
    def _databases(destination: Path) -> None:
        for relative in ("maddy/credentials.db", "maddy/imapsql.db", "dashboard/admin_data.db"):
            path = destination / relative
            if path.exists():
                verify_database(path)

    def close_authentication(self, destination: Path) -> None:
        result_path = destination.with_name(destination.name + ".restore.json")
        result = protected_json(result_path, self.repository.process.owner)
        if (result.get("state") != "verified_staging"
                or result.get("destination") != str(destination)):
            raise ServiceError("verified_restore_staging_required", 409)
        operator = destination / "operator"
        policy = RuntimePolicy(operator / "installation.json",
                               operator / "secrets" / RUNTIME_POLICY_NAME,
                               self.repository.process.owner)
        policy.pause()
        result.update({"state": "authentication_closed", "runtime_policy": "paused",
                       "authentication_prepared": False,
                       "remaining": "Run matched root restore-prepare utility, then SSH recovery"})
        atomic_json(result_path, result)

    def prepare_authentication(self, destination: Path, tools: ImageTools) -> dict[str, Any]:
        lock = destination.with_name(destination.name + ".restore.lock")
        with HostLock(lock, LockPolicy(1, self.repository.process.owner)):
            return self._prepare_authentication(destination, tools)

    def _prepare_authentication(self, destination: Path, tools: ImageTools) -> dict[str, Any]:
        result_path = destination.with_name(destination.name + ".restore.json")
        result = protected_json(result_path, self.repository.process.owner)
        if (result.get("state") not in {"authentication_closed", "authentication_preparing"}
                or result.get("destination") != str(destination)):
            raise ServiceError("closed_restore_staging_required", 409)
        if result["state"] == "authentication_preparing":
            tools.verify_utility_exit(result["utility_name"])
        else:
            name = "mail-admin-recovery-" + str(uuid.uuid4())
            result.update({"state": "authentication_preparing", "utility_name": name})
            atomic_json(result_path, result)
            tools.prepare_restored_authentication(destination, name)
        result.update({"state": "prepared_requires_ssh_recovery", "authentication_prepared": True,
                       "remaining": "Establish fresh password and TOTP through SSH recovery"})
        atomic_json(result_path, result)
        return result

    def recover_authentication(self, destination: Path, tools: ImageTools) -> dict[str, Any]:
        require_private_terminal()
        lock = destination.with_name(destination.name + ".restore.lock")
        with HostLock(lock, LockPolicy(1, self.repository.process.owner)):
            return self._recover_authentication(destination, tools)

    def _recover_authentication(self, destination: Path, tools: ImageTools) -> dict[str, Any]:
        result_path = destination.with_name(destination.name + ".restore.json")
        result = protected_json(result_path, self.repository.process.owner)
        if (result.get("state") not in {"prepared_requires_ssh_recovery", "recovery_in_progress"}
                or result.get("destination") != str(destination)):
            raise ServiceError("prepared_restore_required", 409)
        if result["state"] == "recovery_in_progress":
            tools.verify_recovery(result["recovery_utility_name"], destination)
        else:
            name = "mail-admin-recovery-" + str(uuid.uuid4())
            result.update({"state": "recovery_in_progress", "recovery_utility_name": name})
            atomic_json(result_path, result)
            tools.offline_utility(name, destination)
        result.update({"state": "credentials_recovered_requires_activation",
                       "remaining": "Verify target, reconcile and qualify isolated startup"})
        atomic_json(result_path, result)
        return result


def verify_database(path: Path) -> None:
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ServiceError("restored_database_invalid", 503)
    except sqlite3.Error as error:
        raise ServiceError("restored_database_invalid", 503) from error
