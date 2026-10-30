"""Readable plain-text messages for account verification and operational alerts."""

import json
from datetime import UTC, datetime
from typing import Any

from shared.installation import site


def delivery_test(identifier: str) -> tuple[str, str]:
    return (
        "[Mail Admin] Email delivery test",
        f"Mail Admin email delivery test\n\n"
        f"Service: {site().mail_hostname}\nRecipient: {site().admin_email}\n"
        f"Test ID: {identifier}\n\n"
        "Confirm this test ID in Mail Admin after receiving this message.\n",
    )


def verification_code(code: str) -> tuple[str, str]:
    return (
        "[Mail Admin] Your verification code",
        f"Mail Admin verification\n\nYour verification code is:\n\n{code}\n\n"
        "This code expires in 5 minutes. Do not share it with anyone.\n"
        "If you did not request this code, you can ignore this message.\n",
    )


def operational_alert(event: dict[str, Any]) -> tuple[str, str]:
    name = event["name"].replace("_", " ").capitalize()
    condition = event["condition"].replace("_", " ").capitalize()
    outcome = "Recovered" if event["condition"] == "recovered" else "Action required"
    created = event.get("created")
    observed = (
        datetime.fromtimestamp(created, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        if isinstance(created, (int, float))
        else "Not provided"
    )
    return (
        f"[Mail Admin] {outcome}: {name} - {condition}",
        f"Mail Admin operational notification\n\nService: {site().mail_hostname}\n"
        f"Check: {name}\nStatus: {condition}\nObserved: {observed}\n"
        f"Reference: {event.get('event_id', 'Not provided')}\n\n"
        "Technical details:\n" + json.dumps(event, sort_keys=True, indent=2) + "\n",
    )
