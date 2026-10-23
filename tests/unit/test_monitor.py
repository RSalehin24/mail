import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from host.monitor import Monitor, expiry_condition


class MonitorTests(unittest.TestCase):
    def record(self):
        return {
            "expiry": 60 * 86400,
            "desired_generation": "new",
            "outcome": "activation_pending",
            "consumers": {
                "maddy": {
                    "intent": "expected_running",
                    "active_generation": "old",
                    "served_expiry": 5 * 86400,
                },
                "mail-admin": {
                    "intent": "expected_running",
                    "active_generation": "new",
                    "served_expiry": 60 * 86400,
                },
            },
        }

    def test_new_desired_certificate_does_not_hide_the_expiring_served_generation(self):
        alerts = MagicMock()
        with patch("host.monitor.protected_json", return_value=self.record()):
            Monitor(alerts, ({}, lambda: 0))._certificate()
        alerts.observe.assert_any_call("certificate_expiry", "critical_7_days")
        alerts.observe.assert_any_call("certificate_activation", "pending_or_failed")

    def test_missing_served_expiry_is_unknown_unless_the_recorded_generation_matches(self):
        record = self.record()
        record["consumers"]["maddy"].pop("served_expiry")
        self.assertEqual(expiry_condition(record, 0), "unknown")
        record["consumers"]["maddy"]["active_generation"] = "new"
        self.assertEqual(expiry_condition(record, 0), "recovered")
        record["consumers"]["maddy"] = {"intent": "never_deployed"}
        self.assertEqual(expiry_condition(record, 0), "recovered")

    def test_invalid_expiry_state_reports_unknown_instead_of_retaining_a_healthy_signal(self):
        alerts = MagicMock()
        record = self.record()
        record.pop("expiry")
        with patch("host.monitor.protected_json", return_value=record):
            Monitor(alerts, ({}, lambda: 0))._certificate()
        alerts.observe.assert_any_call("certificate_expiry", "unknown")

    def test_recent_verification_cannot_disguise_a_missed_backup(self):
        now = 1800000000.0
        record = {
            "verified": True,
            "available": True,
            "availability_observed_at": now,
            "cutoff": datetime.fromtimestamp(now - 3601, UTC).isoformat(),
        }
        alerts = MagicMock()
        monitor = Monitor(alerts, ({"inputs": {"recovery_point_seconds": 3600}}, lambda: now))
        with patch("host.monitor.protected_json", return_value=record):
            monitor._backup()
            alerts.observe.assert_called_with("backup", "failed_or_missed")
            record["cutoff"] = datetime.fromtimestamp(now - 1, UTC).isoformat()
            monitor._backup()
            alerts.observe.assert_called_with("backup", "recovered")
            record["availability_observed_at"] = now - 3601
            monitor._backup()
            alerts.observe.assert_called_with("backup", "failed_or_missed")

    def test_future_missing_or_naive_backup_cutoff_cannot_be_healthy(self):
        now = 1800000000.0
        alerts = MagicMock()
        monitor = Monitor(alerts, ({"inputs": {"recovery_point_seconds": 3600}}, lambda: now))
        cutoffs = (
            None, "invalid", "2027-01-15T08:00:00",
            datetime.fromtimestamp(now + 1, UTC).isoformat(),
        )
        for cutoff in cutoffs:
            with self.subTest(cutoff=cutoff):
                record = {
                    "verified": True,
                    "available": True,
                    "availability_observed_at": now,
                    "cutoff": cutoff,
                }
                with patch("host.monitor.protected_json", return_value=record):
                    monitor._backup()
                alerts.observe.assert_called_with("backup", "failed_or_missed")

    def test_external_expiry_uses_each_served_certificate_without_local_dashboard_state(self):
        alerts = MagicMock()
        monitor = Monitor(alerts, ({}, lambda: 0))
        for days, expected in ((30, "recovered"), (21, "warning_21_days"), (7, "critical_7_days")):
            with self.subTest(days=days):
                with patch(
                    "host.monitor.served_certificate", return_value={"expiry": days * 86400}
                ) as probe:
                    monitor.external()
                self.assertEqual(probe.call_count, 2)
                for port in (993, 443):
                    alerts.observe.assert_any_call(f"external_tls_{port}", expected)

    def test_failed_external_consumer_does_not_hide_the_other_consumers_expiry(self):
        alerts = MagicMock()
        with patch(
            "host.monitor.served_certificate",
            side_effect=[OSError("fixture outage"), {"expiry": 5 * 86400}],
        ):
            Monitor(alerts, ({}, lambda: 0)).external()
        alerts.observe.assert_any_call("external_tls_993", "unreachable_or_untrusted")
        alerts.observe.assert_any_call("external_tls_443", "critical_7_days")
        alerts.flush.assert_called_once()
