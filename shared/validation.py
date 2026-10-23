"""Pure policy checks, called independently by API and helper."""

import re
import uuid

from shared.errors import ServiceError

DOMAIN = "rsalehin24.me"
ADMIN_EMAIL = "rsalehin24@gmail.com"
MAIL_HOST = "mail.rsalehin24.me"
OTP_SENDER = "mail@rsalehin24.me"
SCHEMA_VERSION = 1
HELPER_PROTOCOL = 1
BODY_LIMIT = 65536
HELPER_LIMIT = 8192
RESERVED = frozenset({"postmaster", "abuse", "hostmaster", "mailer-daemon"})


def address(value: str) -> str:
    local, separator, domain = value.lower().partition("@")
    valid = re.fullmatch(r"[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?", local)
    if not separator or domain != DOMAIN or len(local) > 64 or not valid or ".." in local:
        raise ServiceError("invalid_address")
    return f"{local}@{domain}"


def new_address(value: str) -> str:
    normalized = address(value)
    if normalized.split("@")[0] in RESERVED:
        raise ServiceError("reserved_address")
    return normalized


def mailbox_password(value: str) -> str:
    if len(value) < 16 or utf8_size(value) > 72:
        raise ServiceError("invalid_mailbox_password")
    if any(character in value for character in "\r\n\0"):
        raise ServiceError("invalid_mailbox_password")
    return value


def master_password(value: str) -> str:
    if len(value) < 16 or utf8_size(value) > 1024:
        raise ServiceError("invalid_master_password")
    return value


def request_id(value: str) -> str:
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError) as error:
        raise ServiceError("invalid_request_id") from error
    if value != normalized:
        raise ServiceError("invalid_request_id")
    return normalized


def bounded_text(value: str, maximum: int) -> str:
    if utf8_size(value) > maximum or any(ord(character) < 32 for character in value):
        raise ServiceError("invalid_text")
    return value


def utf8_size(value: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ServiceError("invalid_unicode", 400) from error


def capability(value: str) -> bool:
    if value not in {"true", "false"}:
        raise ServiceError("invalid_capability", 503)
    return value == "true"
