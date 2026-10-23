import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from host.legacy import LegacyRenewal
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.support import StateFixture


class LegacyWriterTests(unittest.TestCase):
    def guard(self, mounts):
        docker = MagicMock()
        docker.run.side_effect = ["a" * 64 + " surviving-certbot", json.dumps(mounts)]
        return LegacyRenewal(Path("/unused-legacy-record"), 1, docker)

    def verify(self, guard):
        with patch("host.legacy.Path.iterdir", return_value=[]):
            guard.require_no_writers()

    def test_surviving_container_blocks_capture_even_when_its_legacy_parent_is_gone(self):
        guard = self.guard([{"Type": "bind", "Source": "/opt/maddy/letsencrypt", "RW": True}])
        with self.assertRaisesRegex(ServiceError, "legacy_container_writer_active"):
            self.verify(guard)

    def test_parent_or_child_writable_mount_also_blocks_certificate_maintenance(self):
        for source in ("/opt", "/opt/maddy/tls/privkey.pem", "/opt/maddy/letsencrypt_lib"):
            with self.subTest(source=source):
                guard = self.guard([{"Type": "bind", "Source": source, "RW": True}])
                self._blocked(guard)

    def _blocked(self, guard):
        with self.assertRaisesRegex(ServiceError, "legacy_container_writer_active"):
            self.verify(guard)

    def test_read_only_certificate_mounts_and_unrelated_fixture_storage_are_allowed(self):
        guard = self.guard(
            [
                {"Type": "bind", "Source": "/opt/maddy/letsencrypt", "RW": False},
                {"Type": "bind", "Source": "/home/fixture/test-data", "RW": True},
                {"Type": "tmpfs", "Source": "", "RW": True},
            ]
        )
        self.verify(guard)

    def test_unavailable_docker_or_malformed_mount_observation_never_means_quiet(self):
        guard = self.guard([])
        guard.docker.run.side_effect = ServiceError("docker_operation_failed", 503)
        with self.assertRaises(ServiceError):
            self.verify(guard)
        guard = self.guard([{"Source": "/opt/maddy/tls"}])
        with self.assertRaisesRegex(ServiceError, "legacy_mount_observation_invalid"):
            self.verify(guard)

    def test_batch_observation_is_bounded_and_excludes_the_verified_maddy_consumer(self):
        guard = self.guard([])
        guard.docker.run.side_effect = ["a" * 64 + " maddy"]
        self.verify(guard)
        self.assertEqual(guard.docker.run.call_count, 1)
        guard.docker.run.side_effect = ["\n".join("a" * 64 + " fixture" for _ in range(65))]
        with self.assertRaisesRegex(ServiceError, "legacy_container_observation_overloaded"):
            self.verify(guard)


class LegacyCompletionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.record = self.fixture.root / "legacy.json"
        atomic_json(self.record, {"state": "entering", "manual_completion": "unknown"})
        docker = MagicMock()
        docker.run.return_value = ""
        self.guard = LegacyRenewal(self.record, 1, docker)
        self.reader = patch("host.legacy.protected_json", side_effect=self.read)
        self.reader.start()
        self.addCleanup(self.reader.stop)
        self.processes = patch("host.legacy.Path.iterdir", return_value=[])
        self.processes.start()
        self.addCleanup(self.processes.stop)
        self.show = patch.object(self.guard, "_show", side_effect=self.stopped)
        self.show.start()
        self.addCleanup(self.show.stop)

    def read(self, path):
        return protected_json(path, os.getuid())

    def stopped(self, unit):
        return ({"UnitFileState": "disabled", "ActiveState": "inactive"}
                if unit.endswith(".timer") else
                {"ActiveState": "inactive", "Result": "success", "ExecMainStatus": "0"})

    def test_observed_manual_process_persists_unknown_even_after_it_disappears(self):
        atomic_json(self.record, {"state": "entering"})
        process = self.fixture.root / "123"
        process.mkdir()
        (process / "cmdline").write_bytes(b"/bin/sh\0/opt/maddy/renew-cert.sh\0")
        with self.assertRaisesRegex(ServiceError, "legacy_manual_run_active"):
            self.guard._check_process(process)
        record = self.read(self.record)
        self.assertEqual(record["manual_completion"], "unknown")
        self.assertEqual(record["manual_writer_pid"], 123)
        with self.assertRaisesRegex(ServiceError, "legacy_manual_completion_unknown"):
            self.guard.verify()
        self.assertEqual(self.read(self.record), record)

    def test_verified_pair_and_served_reconciliation_keeps_original_exit_unknown(self):
        with (patch("host.legacy.PairValidator") as validator,
              patch("host.legacy.verify_served") as served):
            validator.return_value.validate.return_value = {"served_sha256": "a" * 64}
            self.guard.reconcile_manual()
        served.assert_called_once_with(("127.0.0.1", 993), "a" * 64, 1)
        record = self.read(self.record)
        self.assertEqual(record["manual_completion"], "reconciled")
        self.assertIsNone(record["manual_original_exit_code"])
        self.assertEqual(record["state"], "legacy_drained")
        self.guard.verify()

    def test_bad_pair_failed_serving_or_remaining_writer_cannot_clear_unknown(self):
        for boundary in ("pair", "served", "writer"):
            self.failed_reconciliation(boundary)
            self.assertEqual(self.read(self.record)["manual_completion"], "unknown")

    def failed_reconciliation(self, boundary):
        with (patch("host.legacy.PairValidator") as validator,
              patch("host.legacy.verify_served") as served,
              patch.object(self.guard, "require_no_writers") as writers):
            validator.return_value.validate.return_value = {"served_sha256": "a" * 64}
            failures = {"pair": validator.return_value.validate,
                        "served": served, "writer": writers}
            failures[boundary].side_effect = ServiceError("synthetic_failure", 503)
            with self.assertRaises(ServiceError):
                self.guard.reconcile_manual()

    def test_unknown_persists_in_a_new_guard_and_unknown_record_values_fail_closed(self):
        restarted = LegacyRenewal(self.record, 1, self.guard.docker)
        with patch.object(restarted, "_show", side_effect=self.stopped):
            with self.assertRaisesRegex(ServiceError, "legacy_manual_completion_unknown"):
                restarted.verify()
        atomic_json(self.record, {"state": "entering", "manual_completion": "invented"})
        with self.assertRaises(ServiceError):
            self.guard.verify()
        with self.assertRaises(ServiceError):
            self.guard.reconcile_manual()

    def test_managed_service_can_drain_with_its_observed_exit_without_a_manual_override(self):
        atomic_json(self.record, {"state": "entering"})
        process = self.fixture.root / "123"
        process.mkdir()
        (process / "cmdline").write_bytes(b"/bin/sh\0/opt/maddy/renew-cert.sh\0")
        (process / "cgroup").write_bytes(b"0::/system.slice/maddy-cert-renew.service\n")
        with self.assertRaisesRegex(ServiceError, "legacy_service_writer_active"):
            self.guard._check_process(process)
        self.assertNotIn("manual_completion", self.read(self.record))
        self.guard.verify()

    def test_manual_writer_is_recorded_even_while_the_managed_service_is_also_active(self):
        atomic_json(self.record, {"state": "entering"})
        process = self.fixture.root / "123"
        process.mkdir()
        (process / "cmdline").write_bytes(b"/bin/sh\0/opt/maddy/renew-cert.sh\0")
        (process / "cgroup").write_bytes(b"0::/user.slice/user-1000.slice/session-1.scope\n")
        with (patch("host.legacy.Path.iterdir", return_value=[process]),
              patch.object(self.guard, "_show", return_value={"ActiveState": "active"})):
            with self.assertRaisesRegex(ServiceError, "legacy_manual_run_active"):
                self.guard.verify()
        self.assertEqual(self.read(self.record)["manual_completion"], "unknown")

    def test_reconciliation_entry_requires_its_own_approval_before_host_observation(self):
        from host.cli import baseline_action

        with (patch("host.cli.protected_json", return_value={}),
              patch("host.cli.qualify_preflight"),
              patch("host.cli.operational_guard") as guard,
              patch("host.cli.LegacyRenewal") as legacy):
            guard.return_value.approval.side_effect = ServiceError(
                "operator_approval_required", 403
            )
            with self.assertRaises(ServiceError):
                baseline_action("legacy-reconcile")
        guard.return_value.approval.assert_called_once_with("legacy-reconciliation")
        legacy.assert_not_called()
