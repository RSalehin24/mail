"""Exercise bounded synthetic mailbox sessions; never accepts production login credentials."""

import argparse
import asyncio
import json
import math
import secrets
import time
from pathlib import Path
from typing import Any

import httpx


async def run(
    configuration: dict[str, Any],
    duration: int,
    output: Path,
    users: int = 10,
    attachment_mib: int = 10,
) -> None:
    split = configuration.get("scope") == "isolated-two-host-mail-qualification"
    if configuration.get("scope") not in {
        "isolated-synthetic-webmail",
        "isolated-two-host-mail-qualification",
    }:
        raise RuntimeError("Only an explicitly isolated synthetic fixture is accepted")
    accounts = configuration["accounts"][:users]
    if not 1 <= users <= 10 or len(accounts) != users:
        raise ValueError("The fixture must supply between one and ten requested mailboxes")
    if not 1 <= attachment_mib <= 10:
        raise ValueError("Outgoing test attachments must be between one and ten MiB")
    origin = configuration["origin"] if split else f"http://localhost:{configuration['web_port']}"
    if split and not origin.startswith("https://"):
        raise RuntimeError("Certificate verified HTTPS is required")
    samples: dict[str, list[float]] = {}
    errors = []
    pending_submissions = 0
    search_timeouts = 0
    stop = time.monotonic() + duration

    async def request(
        client: httpx.AsyncClient,
        kind: str,
        path: str,
        body: Any = None,
        binary: bytes | None = None,
    ) -> Any:
        nonlocal pending_submissions, search_timeouts
        started = time.monotonic()
        try:
            response = await client.request(
                "PUT" if binary is not None else "GET" if body is None else "POST",
                path,
                **(
                    {"content": binary, "headers": {"Content-Type": "application/octet-stream"}}
                    if binary is not None
                    else {"json": body}
                    if body is not None
                    else {}
                ),
            )
            elapsed = time.monotonic() - started
            samples.setdefault(kind, []).append(elapsed)
            if (
                response.status_code == 504
                and kind == "search"
                and response.json().get("error") == "search_timeout"
            ):
                search_timeouts += 1
                return None
            if (
                response.status_code == 503
                and kind == "sending"
                and response.json().get("error") == "operation_pending"
            ):
                # The real composer observes this durable operation rather than
                # submitting again while the worker finishes its recorded steps.
                pending_submissions += 1
                return {"id": body["operation_id"], "state": "submitting"}
            if not response.is_success:
                errors.append(
                    {
                        "kind": kind,
                        "status": response.status_code,
                        "error": response.json().get("error"),
                    }
                )
                return None
            return (
                response.json()
                if "json" in response.headers.get("content-type", "")
                else response.content
            )
        except (httpx.HTTPError, ValueError) as error:
            errors.append({"kind": kind, "error": type(error).__name__})
            return None

    async def user(index: int, email: str) -> None:
        client = clients[index]
        labels = await request(
            client,
            "label",
            "/api/webmail/labels",
            {"name": f"Load {index} {run_id}", "color": "#123456"},
        )
        cycles = 0
        sent = False
        operation_started = False
        terminal_failure = False
        upload = None
        operation_id = secrets.token_hex(16)
        while time.monotonic() < stop:
            data = await request(
                client,
                "listing",
                "/api/webmail/messages/search",
                {"folder": "INBOX", "page": 1, "size": 25},
            )
            if data and data["messages"]:
                message = await request(
                    client,
                    "opening",
                    "/api/webmail/messages/open",
                    data["messages"][cycles % min(10, len(data["messages"]))]["ref"],
                )
                if message and message["attachments"] and cycles % 4 == 0:
                    part = message["attachments"][0]["part"]
                    ref = message["ref"]
                    url = str(httpx.URL(f"/api/webmail/attachments/{part}", params=ref))
                    await request(client, "download", url)
            if cycles % 3 == 0:
                await request(
                    client,
                    "search",
                    "/api/webmail/messages/search",
                    {"folder": None, "body": "synthetic", "unread": False},
                )
            if not sent and not terminal_failure and cycles >= index % 3:
                if upload is None:
                    upload = await request(
                        client,
                        "upload",
                        "/api/webmail/uploads?filename=synthetic.bin",
                        binary=b"a"
                        * ((attachment_mib if split or index == users - 1 else 1) * 1024 * 1024),
                    )
                if operation_started:
                    operation = await request(
                        client, "sending_status", "/api/webmail/sending/" + operation_id
                    )
                elif upload:
                    operation = await request(
                        client,
                        "sending",
                        "/api/webmail/send",
                        {
                            "operation_id": operation_id,
                            "to": accounts[(index + 1) % users],
                            "subject": f"Synthetic load send {index}",
                            "text": "Local fixture only",
                            "uploads": [upload["id"]],
                        },
                    )
                else:
                    operation = None
                if operation:
                    operation_started = True
                    sent = operation["state"] == "complete"
                    if operation["state"] not in {
                        "complete",
                        "preparing",
                        "submitting",
                        "accepted",
                        "saving_sent",
                        "sent_saved",
                    }:
                        errors.append({"kind": "sending_state", "state": operation["state"]})
                        terminal_failure = True
            if labels and data and data["messages"] and cycles % 4 == 0:
                await request(
                    client,
                    "bulk",
                    "/api/webmail/messages/bulk",
                    {
                        "messages": [data["messages"][0]["ref"]],
                        "action": "label",
                        "label": labels["id"],
                    },
                )
            cycles += 1
            await asyncio.sleep(1 + index * 0.1)
        if not sent:
            errors.append({"kind": "missing_send", "user": index})

    async def administrator() -> None:
        async with httpx.AsyncClient(
            base_url=configuration.get("management_origin", origin),
            headers={
                "Origin": configuration.get("management_origin", origin),
                "Cookie": "__Host-mail_admin=" + configuration["admin_token"],
            },
            timeout=10,
        ) as client:
            while time.monotonic() < stop:
                await request(client, "administrator", "/api/accounts")
                await asyncio.sleep(0.5)

    clients = [
        httpx.AsyncClient(base_url=origin, headers={"Origin": origin}, timeout=45 if split else 15)
        for _ in accounts
    ]
    run_id = secrets.token_hex(4)
    try:
        for client, email in zip(clients, accounts, strict=True):
            response = await client.post(
                "/api/mailbox/login" if split else "/api/login",
                json={"email": email, "password": configuration["password"]},
            )
            if response.status_code != 200 or not response.json().get("authenticated"):
                raise RuntimeError("Synthetic mailbox login failed: " + str(response.status_code))
            client.headers["Cookie"] = response.headers["set-cookie"].split(";")[0]
            warmed = await request(
                client,
                "warmup",
                "/api/webmail/messages/search",
                {"folder": "INBOX", "page": 1, "size": 25},
            )
            if warmed is None or warmed["total"] < configuration["messages_per_mailbox"]:
                raise RuntimeError("Synthetic mailbox preparation did not meet message count")
        measured_start = time.monotonic()
        stop = measured_start + duration
        await asyncio.gather(administrator(), *(user(i, email) for i, email in enumerate(accounts)))
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))

    def summary(values: list[float]) -> dict[str, Any]:
        ordered = sorted(values)
        return {
            "count": len(values),
            "p95_seconds": ordered[max(0, math.ceil(len(values) * 0.95) - 1)],
            "max_seconds": max(values),
        }

    result = {
        "scope": configuration["scope"],
        "users": users,
        "largest_outgoing_attachment_MiB": attachment_mib,
        "pending_submissions_observed": pending_submissions,
        "explicit_search_timeouts": search_timeouts,
        "duration_seconds": duration,
        "measured_elapsed_seconds": time.monotonic() - measured_start,
        "seed_messages": configuration["messages_per_mailbox"] * len(configuration["accounts"]),
        "active_mailbox_seed_messages": configuration["messages_per_mailbox"] * users,
        "seed_method": configuration.get("seed_method", "individual APPEND"),
        "timings": {key: summary(value) for key, value in samples.items()},
        "errors": errors,
    }
    result["latency_pass"] = all(
        result["timings"].get(key, {}).get("p95_seconds", 999) < 3
        for key in ("listing", "opening", "administrator")
    )
    result["search_deadline_pass"] = result["timings"].get("search", {}).get(
        "max_seconds", 999
    ) <= (10 if split else 10.5)
    result["application_pass"] = (
        not errors and result["latency_pass"] and result["search_deadline_pass"]
    )
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--users", type=int, choices=range(1, 11), default=10)
    parser.add_argument("--attachment-mib", type=int, choices=range(1, 11), default=10)
    args = parser.parse_args()
    configuration = json.loads(args.fixture.read_text())
    asyncio.run(run(configuration, args.duration, args.output, args.users, args.attachment_mib))


if __name__ == "__main__":
    main()
