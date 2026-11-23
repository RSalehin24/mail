"""Boot-time update of the mail host's Cloudflare A record to the VM's current external IP.

The ephemeral external IP changes whenever the VM is stopped and started. Only the `mail` A
record depends on it (SPF has no address, the delivery MX points at that name), so this reads the
address from the metadata server and changes that one record when it differs.
"""

import argparse
import ipaddress
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from helper.cloudflare_forwarding import Cloudflare
from shared.errors import ServiceError
from shared.files import protected_bytes, protected_json

CONFIGURATION = Path("/etc/mail-admin/dns-update.json")
METADATA_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/"
    "network-interfaces/0/access-configs/0/external-ip"
)
SETTING_FIELDS = ("account_id", "zone_id", "record_name", "token_reference")
RECORD_NAME = re.compile(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?")
RECORD_ID = re.compile(r"[a-f0-9]{32}")
ATTEMPTS = 5
RETRY_SECONDS = 15


def external_address() -> str:
    request = urllib.request.Request(METADATA_URL, headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 -- fixed http URL.
            text = response.read(64).decode().strip()
        value = ipaddress.IPv4Address(text)
    except (OSError, ValueError) as error:
        raise ServiceError("external_address_unavailable", 503) from error
    if not value.is_global:
        raise ServiceError("external_address_invalid", 503)
    return str(value)


def load_settings(path: Path) -> dict[str, str]:
    settings = protected_json(path)
    if any(type(settings.get(field)) is not str for field in SETTING_FIELDS):
        raise ServiceError("dns_update_configuration_invalid", 503)
    if RECORD_NAME.fullmatch(settings["record_name"]) is None:
        raise ServiceError("dns_update_configuration_invalid", 503)
    return {field: settings[field] for field in SETTING_FIELDS}


def current_record(api: Cloudflare, name: str) -> dict[str, Any]:
    # One request with its own query: listing() appends paging parameters to the path.
    answer = api.request("GET", f"/zones/{api.zone}/dns_records?type=A&name={name}&per_page=5")
    rows = answer.get("result")
    # Exactly one record: creating or choosing between records is the owner's decision.
    if not isinstance(rows, list) or len(rows) != 1:
        raise ServiceError("dns_record_not_unique", 503)
    record: dict[str, Any] = rows[0]
    if RECORD_ID.fullmatch(str(record.get("id"))) is None:
        raise ServiceError("dns_record_invalid", 503)
    return record


def synchronize(api: Cloudflare, name: str, address: str, dry_run: bool) -> str:
    record = current_record(api, name)
    if record.get("content") == address:
        return f"unchanged {name} {address}"
    if dry_run:
        return f"would change {name} {record.get('content')} -> {address}"
    api.request(
        "PUT", f"/zones/{api.zone}/dns_records/{record['id']}",
        {"type": "A", "name": name, "content": address,
         "ttl": record.get("ttl", 1), "proxied": record.get("proxied", False)},
    )
    return f"changed {name} {record.get('content')} -> {address}"


def run_once(dry_run: bool) -> str:
    settings = load_settings(CONFIGURATION)
    token = protected_bytes(Path(settings["token_reference"])).decode().strip()
    api = Cloudflare(settings["account_id"], settings["zone_id"], token)
    return synchronize(api, settings["record_name"], external_address(), dry_run)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the change, make none")
    arguments = parser.parse_args()
    for attempt in range(1, ATTEMPTS + 1):
        try:
            print(run_once(arguments.dry_run))
            return 0
        except (ServiceError, OSError) as error:
            # The network may not be up at boot; the error class is logged, never a secret.
            print(f"dns update attempt {attempt} failed: {error}", file=sys.stderr)
            if attempt < ATTEMPTS:
                time.sleep(RETRY_SECONDS)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
