"""Fail closed release gates for measured two-host capacity evidence."""

from pathlib import Path
from typing import Any

from applications import VERSION
from backend.database import Database
from shared.errors import ServiceError
from shared.files import protected_json


def validate(report: dict[str, Any]) -> None:
    def require(condition: bool) -> None:
        if not condition:
            raise ServiceError("two_server_qualification_required", 409)

    require(report.get("scope") == "isolated-two-host-mail-qualification")
    require(report.get("simulation") is not True)
    require(report.get("version") == VERSION and report.get("users") == 10)
    require(report.get("messages", 0) >= 10000 and report.get("duration_seconds", 0) >= 120)
    require(report.get("attachment_bytes") == 10 * 1024**2)
    require(report.get("unexpected_errors") == 0 and report.get("max_active_users") == 10)
    hosts = report.get("hosts", {})
    require(set(hosts) == {"management", "webmail"})
    identities = []
    for host in hosts.values():
        require(isinstance(host.get("machine_id"), str) and len(host["machine_id"]) >= 16)
        identities.append(host["machine_id"])
        require(900 <= host.get("memory_total_mib", 0) <= 1100)
        require(host.get("available_memory_min_bytes", 0) >= 128 * 1024**2)
        require(
            host.get("oom_kills_after") == host.get("oom_kills_before")
            and type(host.get("oom_kills_before")) is int
        )
        require(bool(host.get("containers")))
        for container in host["containers"].values():
            require(container.get("oom_killed") is False and container.get("error_count") == 0)
            require(
                container.get("memory_limit_bytes", 0) > 0
                and container.get("peak_memory_bytes", 0) > 0
            )
        require(host.get("monitor_samples", 0) >= 120 and host.get("installation_id") is not None)
    require(len(set(identities)) == 2)
    timings = report.get("timings", {})
    for operation in ("listing", "opening", "administrator"):
        require(timings.get(operation, {}).get("count", 0) >= 10)
        require(timings[operation].get("p95_seconds", 999) < 3)
    require(timings.get("search", {}).get("count", 0) >= 10)
    require(timings["search"].get("max_seconds", 999) <= 10)
    require(timings.get("upload", {}).get("count", 0) >= 10)
    require(timings.get("download", {}).get("count", 0) >= 10)
    require(timings.get("sending", {}).get("count", 0) >= 10)
    require(report.get("maintenance_cycles", 0) > 0)
    required_cases = {
        "fresh_install",
        "adoption",
        "pairing",
        "rotation_revocation",
        "upgrade",
        "disconnection",
        "restoration",
        "mailbox_isolation",
        "administrator_denial",
        "mfa_recovery",
        "password_change",
        "recreation",
        "session_revocation",
        "native_imap_smtp",
        "hostile_html",
        "attachment_interruption_limits",
        "uidvalidity",
        "partial_failure",
        "logged_out_trash",
    }
    cases = report.get("cases", {})
    require(all(cases.get(case) == "pass" for case in required_cases))


def require_qualified(path: Path, database: Database, management_id: str = "") -> None:
    try:
        report = protected_json(path)
        validate(report)
        if (
            report["hosts"]["webmail"]["installation_id"] != database.identity
            or management_id
            and report["hosts"]["management"]["installation_id"] != management_id
        ):
            raise ServiceError("qualification_installation_mismatch", 409)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ServiceError("two_server_qualification_required", 409) from error
