"""Operator-only preparation for the verified local-delivery subdomain."""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from host.cloudflare_forwarding import CONFIGURATION, Cloudflare, validate_configuration
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json
from shared.installation import site


def prepare(api: Cloudflare, configuration: dict[str, Any], mailboxes: list[str]) -> dict[str, Any]:
    validate_configuration(configuration)
    records = api.listing(f"/zones/{api.zone}/dns_records")
    primary = [row for row in records if row["type"] == "MX" and row["name"] == site().domain]
    if not primary or not all(
        str(row["content"]).endswith(".mx.cloudflare.net") for row in primary
    ):
        raise ServiceError("primary_cloudflare_mx_not_confirmed", 503)
    aliases = [
        row
        for row in records
        if row["type"] == "MX" and row["name"] == configuration["delivery_domain"]
    ]
    if aliases and (len(aliases) != 1 or aliases[0]["content"] != site().mail_hostname):
        raise ServiceError("delivery_mx_conflict", 409)
    if not aliases:
        api.request(
            "POST",
            f"/zones/{api.zone}/dns_records",
            {
                "type": "MX",
                "name": configuration["delivery_domain"],
                "content": site().mail_hostname,
                "priority": 10,
                "ttl": 300,
            },
        )
    known = api.listing(f"/accounts/{api.account}/email/routing/addresses")
    addresses = {row["email"]: row for row in known}
    local = []
    for email in mailboxes:
        if not re.fullmatch(r"[a-z0-9_.-]{1,64}@" + re.escape(site().domain), email):
            raise ServiceError("invalid_local_delivery_mailbox", 400)
        alias = email.split("@", 1)[0] + "@" + configuration["delivery_domain"]
        if alias not in addresses:
            result = api.request(
                "POST", f"/accounts/{api.account}/email/routing/addresses", {"email": alias}
            )
            addresses[alias] = result["result"]
        local.append({"email": alias, "verified": bool(addresses[alias].get("verified"))})
    return {"primary_mx_preserved": True, "local_destinations": local}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mailboxes", nargs="+")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    values = protected_json(CONFIGURATION)
    validate_configuration(values)
    token = protected_bytes(Path(values["token_reference"])).decode().strip()
    result = prepare(
        Cloudflare(values["account_id"], values["zone_id"], token), values, args.mailboxes
    )
    atomic_json(Path("/var/lib/mail-admin-helper/cloudflare-preparation.json"), result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
