"""Explicit measured runtime settings; no replacement keys or database defaults."""

import math
import os
from dataclasses import dataclass
from pathlib import Path

from shared.errors import ServiceError
from shared.installation import site
from shared.validation import capability


@dataclass(frozen=True)
class Settings:
    origin: str
    static_directory: Path
    hash_memory_kib: int
    hash_parallelism: int
    http_concurrency: int
    operation_timeout: float
    email_otp_enabled: bool = False
    permanent_deletion_enabled: bool = False
    webmail_enabled: bool = False
    webmail_allowed_mailboxes: tuple[str, ...] = ()

    application_kind: str = "legacy"

    @classmethod
    def from_environment(cls) -> "Settings":
        values = [
            int(os.environ[key])
            for key in ("HASH_MEMORY_KIB", "HASH_PARALLELISM", "HTTP_CONCURRENCY")
        ]
        timeout = float(os.environ["OPERATION_TIMEOUT_SECONDS"])
        if min(values) <= 0 or timeout <= 0 or not math.isfinite(timeout):
            raise ServiceError("invalid_resource_configuration", 503)
        return cls(
            f"https://{site().mail_hostname}",
            Path("/app/static"),
            values[0],
            values[1],
            values[2],
            timeout,
            capability(os.environ.get("EMAIL_OTP_ENABLED", "false")),
            capability(os.environ.get("PERMANENT_DELETION_ENABLED", "false")),
            capability(os.environ.get("WEBMAIL_ENABLED", "false")),
            cls._webmail_allowlist(),
        )

    @staticmethod
    def _webmail_allowlist() -> tuple[str, ...]:
        from shared.validation import address

        values = os.environ.get("WEBMAIL_ALLOWED_MAILBOXES", "")
        return tuple(address(value.strip()) for value in values.split(",") if value.strip())
