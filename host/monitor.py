"""Host-independent alert conditions; polling performs no email/relay tests."""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from host.alerts import Alerts
from host.certificates import served_certificate
from host.rollout import timestamp
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
        self._certificate()
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

    def _certificate(self) -> None:
        try:
            record = protected_json(Path("/var/lib/mail-admin-certificates/state.json"))
            self.alerts.observe("certificate_expiry", expiry_condition(record, self.clock()))
            self.alerts.observe(
                "certificate_activation",
                "recovered" if record["outcome"] == "active" else "pending_or_failed",
            )
        except (OSError, ValueError, KeyError, TypeError, ServiceError):
            self.alerts.observe("certificate_expiry", "unknown")
            self.alerts.observe("certificate_activation", "unknown")

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
