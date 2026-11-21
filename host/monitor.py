"""Host-independent alert conditions; polling performs no email/relay tests."""

import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from host.alerts import Alerts
from host.certificates import served_certificate
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.validation import MAIL_HOST


def expiry_condition(record: dict[str, Any], now: float) -> str:
    expiries = [record["expiry"]]
    for consumer in record.get("consumers", {}).values():
        if consumer.get("intent") == "never_deployed":
            continue
        expiry = consumer.get("served_expiry")
        desired = record.get("desired_generation")
        if expiry is None and desired and consumer.get("active_generation") == desired:
            expiry = record["expiry"]
        if not isinstance(expiry, (int, float)) or isinstance(expiry, bool):
            return "unknown"
        expiries.append(expiry)
    days = (min(expiries) - now) / 86400
    return "critical_7_days" if days <= 7 else "warning_21_days" if days <= 21 else "recovered"


def timestamp(value: Any) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone required")
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError) as error:
        raise ServiceError("invalid_backup_timestamp", 409) from error


class Monitor:
    def __init__(
        self, alerts: Alerts, dependencies: tuple[dict[str, Any], Callable[[], float]],
        reports: Path | None = None,
    ) -> None:
        self.reports = reports
        self.alerts = alerts
        self.configuration, self.clock = dependencies

    def host(self) -> None:
        inputs = self.configuration["inputs"]
        disk = os.statvfs("/opt/maddy/data")
        condition = (
            "low"
            if disk.f_bavail * disk.f_frsize < inputs["disk_free_minimum_bytes"]
            or disk.f_favail < inputs["inode_free_minimum"]
            else "recovered"
        )
        self.alerts.observe("host_capacity", condition)
        self._backup()
        self._report("monitor-observation.json", {"outcome": "checks_completed"})
        self.alerts.flush()

    def _backup(self) -> None:
        try:
            record = protected_json(Path("/var/lib/mail-admin-backup/manifest.json"))
            now = self.clock()
            availability = record["availability_observed_at"]
            age = now - timestamp(record["cutoff"])
            maximum = self.configuration["inputs"]["recovery_point_seconds"]
            healthy = (
                record["verified"] is True
                and record["available"] is True
                and type(availability) in {int, float}
                and 0 <= now - availability <= maximum
                and 0 <= age <= maximum
            )
        except (OSError, ValueError, KeyError, TypeError, ServiceError):
            healthy = False
        self.alerts.observe("backup", "recovered" if healthy else "failed_or_missed")

    def external(self) -> None:
        consumers: dict[str, Any] = {}
        for port in (993, 443):
            try:
                certificate = served_certificate((MAIL_HOST, port))
                condition = expiry_condition(certificate, self.clock())
                consumers["maddy" if port == 993 else "mail-admin"] = {
                    "state": "tls_verified", "served_expiry": certificate["expiry"],
                    "served_sha256": certificate.get("served_sha256"),
                }
            except (OSError, ValueError, KeyError, TypeError, ServiceError):
                condition = "unreachable_or_untrusted"
                consumers["maddy" if port == 993 else "mail-admin"] = {
                    "state": "unknown", "reason": condition,
                }
            self.alerts.observe(f"external_tls_{port}", condition)
        self._report("served-certificates.json", {"consumers": consumers})
        self.alerts.flush()


    def _report(self, name: str, record: dict[str, Any]) -> None:
        if self.reports is not None:
            atomic_json(self.reports / name, {**record, "observed_at": self.clock()})
