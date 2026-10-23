"""Read only provider-reported credit fields; never return account contact details."""

import http.client
import json
import ssl
from typing import Any

from shared.errors import ServiceError


def credit_fields(account: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for plan in account.get("plan", []):
        if not isinstance(plan, dict) or type(plan.get("credits")) is not int:
            continue
        if plan["credits"] < 0 or plan.get("type") == "sms":
            continue
        kind, credits_type = plan.get("type"), plan.get("creditsType")
        if isinstance(kind, str) and isinstance(credits_type, str):
            result.append(
                {"plan": kind[:64], "credits": plan["credits"], "credits_type": credits_type[:64]}
            )
    return result


def read_quota(api_key: str) -> dict[str, Any]:
    connection = http.client.HTTPSConnection(
        "api.brevo.com", 443, timeout=5, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "GET", "/v3/account", headers={"api-key": api_key, "accept": "application/json"}
        )
        response = connection.getresponse()
        content = response.read(65537)
        if response.status != 200 or len(content) > 65536:
            raise ServiceError("quota_unavailable", 502)
        account = json.loads(content)
        if not isinstance(account, dict):
            raise ServiceError("quota_unavailable", 502)
        return {"state": "observed", "plans": credit_fields(account)}
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise ServiceError("quota_unavailable", 502) from error
    finally:
        connection.close()
