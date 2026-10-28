"""Application composition root; the runtime has no Docker or Maddy filesystem dependency."""

import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher

from backend.auth import AuthDependencies, Authentication
from backend.binds import require_runtime_sources
from backend.config import Settings
from backend.database import Database
from backend.dns import observe_mx
from backend.helper_client import HelperClient
from backend.mailboxes import Mailboxes
from backend.relay import Relay, RelayDependencies, SmtpTransport
from backend.secrets import SecretKeys
from backend.work import WorkPool
from shared.errors import ServiceError
from shared.files import protected_json
from shared.runtime_policy import RuntimePolicy
from shared.validation import SCHEMA_VERSION


@dataclass
class Services:
    settings: Settings
    authentication: Authentication
    mailboxes: Mailboxes
    hashes: WorkPool
    io: WorkPool
    relay: Relay
    dns: Callable[[], dict[str, Any]] = observe_mx

    @classmethod
    def production(cls) -> "Services":
        RuntimePolicy().require_running()
        require_runtime_sources()
        return cls._load()

    @classmethod
    def operator(cls) -> "Services":
        if os.geteuid() != 0:
            raise ServiceError("root_required", 403)
        return cls._load()

    @classmethod
    def _load(cls) -> "Services":
        settings = Settings.from_environment()
        marker = Path("/run/mail-admin-installation.json")
        installation = protected_json(marker)
        if (
            type(installation.get("schema_version")) is not int
            or installation["schema_version"] != SCHEMA_VERSION
        ):
            raise ServiceError("installation_schema_mismatch", 503)
        keys = SecretKeys.load(Path("/run/secrets/mail-admin"), marker)
        database = Database(Path("/app/data/admin_data.db"), installation["installation_id"])
        hasher = PasswordHasher(memory_cost=settings.hash_memory_kib, time_cost=3, parallelism=1)
        authentication = Authentication(
            AuthDependencies(database, keys, hasher, time.time, settings.email_otp_enabled)
        )
        helper = HelperClient(
            Path("/run/mail-admin-helper/control.sock"), settings.operation_timeout
        )
        def synchronize(values: dict[str, Any]) -> None:
            result = helper.call({"operation": "submission_sync", "request_id": str(uuid.uuid4()),
                                  **values})
            if result.get("synchronized") is not True:
                raise ServiceError("submission_synchronization_failed", 503)

        def reconcile() -> None:
            helper.call({"operation": "submission_reconcile", "request_id": str(uuid.uuid4())})

        return cls(
            settings,
            authentication,
            Mailboxes((database, helper, time.time)),
            WorkPool(settings.hash_parallelism, settings.operation_timeout),
            WorkPool(settings.http_concurrency, settings.operation_timeout),
            Relay(
                RelayDependencies(
                    database, keys, SmtpTransport(settings.operation_timeout),
                    time.time, synchronize, reconcile
                )
            ),
        )

    def close(self) -> None:
        self.hashes.close()
        self.io.close()
