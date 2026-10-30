"""Validated installation values. Product code has no customer or provider defaults."""

import json
import os
import re
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from shared.errors import ServiceError


def hostname(value: str) -> str:
    if not isinstance(value, str):
        raise ServiceError("invalid_hostname", 400)
    value = value.lower()
    if (
        len(value) > 253
        or "." not in value
        or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        )
    ):
        raise ServiceError("invalid_hostname", 400)
    return value


def email_address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254:
        raise ServiceError("invalid_email", 400)
    local, separator, domain = value.lower().partition("@")
    if not separator or not re.fullmatch(r"[a-z0-9._+\-]{1,64}", local):
        raise ServiceError("invalid_email", 400)
    return local + "@" + hostname(domain)


@dataclass(frozen=True)
class Site:
    domain: str
    mail_hostname: str
    admin_email: str
    hostname: str
    relay_host: str
    delivery_domain: str = ""

    def __post_init__(self) -> None:
        for field in ("domain", "mail_hostname", "hostname"):
            if hostname(getattr(self, field)) != getattr(self, field):
                raise ServiceError("noncanonical_configuration", 400)
        if self.relay_host:
            hostname(self.relay_host)
        if self.delivery_domain:
            hostname(self.delivery_domain)
        if email_address(self.admin_email) != self.admin_email:
            raise ServiceError("noncanonical_configuration", 400)

    @property
    def otp_sender(self) -> str:
        return "mail@" + self.domain

    def public(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def parse(cls, values: dict[str, Any]) -> "Site":
        try:
            return cls(**values)
        except (TypeError, AttributeError) as error:
            raise ServiceError("invalid_installation_configuration", 503) from error


@lru_cache(maxsize=8)
def _read(path: str, modified: int, size: int) -> Site:
    with Path(path).open() as stream:
        values = json.load(stream)
    return Site.parse(values)


def site() -> Site:
    path = Path(os.environ.get("MAIL_CONFIG", "/etc/mail-admin/site.json"))
    try:
        metadata = path.stat()
        if path.is_symlink() or metadata.st_mode & 0o022:
            raise ServiceError("unsafe_installation_configuration", 503)
        return _read(str(path), metadata.st_mtime_ns, metadata.st_size)
    except (OSError, ValueError) as error:
        raise ServiceError("installation_configuration_required", 503) from error
