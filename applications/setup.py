"""Token protected browser configuration. DNS and provider prerequisites remain explicit."""

import ipaddress
import json
import os
import ssl
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from pydantic import Field

from backend.enrollment import Enrollment
from backend.requests import RequestModel
from backend.services import Services
from shared.errors import ServiceError
from shared.files import atomic_json, sync_directory
from shared.installation import Site, hostname


class ConfigureRequest(RequestModel):
    token: str = Field(max_length=128)
    domain: str = Field(max_length=253)
    hostname: str = Field(max_length=253)
    mail_hostname: str = Field(max_length=253)
    admin_email: str = Field(max_length=254)
    relay_host: str = Field(default="", max_length=253)
    relay_username: str = Field(default="", max_length=256)
    relay_password: str = Field(default="", max_length=4096)
    backup_destination: str = Field(max_length=512)
    public_ipv4: str = Field(default="", max_length=45)
    spf_value: str = Field(default="", max_length=1024)
    dkim_name: str = Field(default="", max_length=253)
    dkim_value: str = Field(default="", max_length=4096)
    fullchain: str = Field(max_length=16000)
    private_key: str = Field(max_length=16000)


def validate_tls(root: Path, certificate: str, private_key: str, names: list[str]) -> None:
    try:
        leaf = x509.load_pem_x509_certificate(certificate.encode())
        key = serialization.load_pem_private_key(private_key.encode(), password=None)

        def public(item: Any) -> bytes:
            return bytes(
                item.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )

        if public(leaf.public_key()) != public(key.public_key()):
            raise ValueError("Key mismatch")
        now = datetime.now(UTC)
        if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
            raise ValueError("Certificate expired")
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        published = san.get_values_for_type(x509.DNSName)
        if not all(
            name in published or ("*." + name.split(".", 1)[-1]) in published for name in names
        ):
            raise ValueError("Certificate does not cover hostname")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Validate the complete PEM chain and private key using OpenSSL's parser.
        staging = root / "tls" / uuid.uuid4().hex
        staging.mkdir(mode=0o700, exist_ok=False)
        try:
            (staging / "fullchain.pem").write_text(certificate)
            (staging / "privkey.pem").write_text(private_key)
            for path in staging.iterdir():
                path.chmod(0o600)
            context.load_cert_chain(staging / "fullchain.pem", staging / "privkey.pem")
            link = root / "tls" / (".active-" + uuid.uuid4().hex)
            link.symlink_to(staging.name)
            os.replace(link, root / "tls/active")
            sync_directory(root / "tls")
        except BaseException:
            for path in staging.iterdir():
                path.unlink()
            staging.rmdir()
            raise
    except (ValueError, TypeError, x509.ExtensionNotFound, ssl.SSLError) as error:
        raise ServiceError("invalid_tls_certificate_or_key", 400) from error


def configure(context: Services, root: Path, body: ConfigureRequest, peer: str) -> dict[str, Any]:
    # Token verification also checks that initialized installations cannot reopen setup.
    Enrollment(context.authentication).begin(body.token, peer)
    kind = context.settings.application_kind
    config = Site(body.domain, body.mail_hostname, body.admin_email, body.hostname, body.relay_host)
    if body.public_ipv4:
        ipaddress.IPv4Address(body.public_ipv4)
    backup_root = Path("/var/backups/mail-" + kind)
    if (
        not Path(body.backup_destination).is_relative_to(backup_root)
        or ".." in Path(body.backup_destination).parts
    ):
        raise ServiceError("absolute_backup_destination_required", 400)
    if kind == "management":
        hostname(body.relay_host)
        if (
            not body.relay_username
            or not body.relay_password
            or not body.spf_value.startswith("v=spf1 ")
        ):
            raise ServiceError("relay_and_provider_spf_required", 400)
        # Keep Maddy configuration tokens closed; credentials never become configuration syntax.
        from shared.submission import validate_credentials

        validate_credentials({"username": body.relay_username, "credential": body.relay_password})
    values = {
        "backup_destination": body.backup_destination,
        "public_ipv4": body.public_ipv4,
        "spf_value": body.spf_value,
        "dkim_name": body.dkim_name,
        "dkim_value": body.dkim_value,
        "webmail_enabled": False,
        "allowlist": [],
        "delivery_mode": "direct",
    }
    names = [config.hostname] + ([config.mail_hostname] if kind == "management" else [])
    validate_tls(root, body.fullchain, body.private_key, names)
    atomic_json(root / "site.json", config.public())
    with context.authentication.database.transaction() as connection:
        connection.execute(
            "UPDATE application_state SET configured=1,settings=? WHERE id=1",
            (json.dumps(values),),
        )
    if kind == "management":
        context.relay.save(
            {
                "host": config.relay_host,
                "port": 587,
                "tls_mode": "starttls",
                "username": body.relay_username,
                "credential": body.relay_password,
                "api_key": "",
            }
        )
    (root / "restart-requested").touch(mode=0o600)
    return {"configured": True, "restart_required": True, "webmail_enabled": False}
