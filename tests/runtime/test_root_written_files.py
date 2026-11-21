"""The dashboard (uid 10001, read-only, no Docker) reads what the root workers write.

Every file is written by root with `atomic_json`, then given to the service group once, which is
what the cutover does. The workers' later rewrites must keep it readable.
"""

import os
import stat
import time
import unittest

from shared.files import atomic_json
from tests.maddy_support import SERVICE_ID, UnprivilegedMaddyFixture
from tests.recovery_support import qualify_fixture
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture

THRESHOLDS = {"disk_free_minimum_bytes": 1024, "inode_free_minimum": 10,
              "recovery_point_seconds": 93600}
CONSUMERS = {"maddy": {"intent": "expected_running", "state": "active"},
             "mail-admin": {"intent": "expected_running", "state": "active"}}
POLICY = {"schema_version": 1, "revision": 1, "enabled": True,
          "permanent_deletion_enabled": False, "default_retention": "7_days",
          "protected": {"mail@rsalehin24.me": "Administrator verification emails"}}


class RootWrittenFilesTests(unittest.TestCase):
    def setUp(self):
        maddy = UnprivilegedMaddyFixture()
        self.addCleanup(maddy.close)
        maddy.start()
        self.web = RuntimeFixture(maddy, THRESHOLDS)
        self.addCleanup(self.web.close)
        self.write_observations()
        self.write_forwarding_and_recovery_records()
        self.web.start()
        authentication = RuntimeAuthentication(self, self.web)
        authentication.enroll()
        authentication.login()

    def root_write(self, path, record):
        atomic_json(path, record)
        RuntimeFixture.grant_dashboard(path)

    def observation(self, **fields):
        return {"observed_at": time.time(), "outcome": "active", **fields}

    def write_observations(self):
        directories = self.web.observation_directories
        for directory, name, record in (
            ("alerts", "monitor-observation.json", self.observation()),
            ("alerts", "external-monitoring.json", self.observation(provider="external")),
            ("alerts", "served-certificates.json", self.observation(consumers=CONSUMERS)),
        ):
            self.root_write(directories[directory] / name, record)
        self.root_write(self.web.records["manifest.json"], self.observation(
            cutoff="2026-11-21T00:00:00+00:00", verified=True, available=True,
            restore_passed=True, availability_observed_at=time.time(),
        ))

    def write_forwarding_and_recovery_records(self):
        state = self.web.state
        self.root_write(state / "cloudflare-forwarding.json", self.observation(
            configured=True, state="connected", revision=0, mailboxes={},
            verified_destinations=[], connected_mailboxes=[],
        ))
        self.root_write(state / "forwarding-worker-status.json", self.observation())
        atomic_json(state / "recovery-policy.json", POLICY)
        # The dashboard edits the policy, so it belongs to the state owner like the rest.
        os.chown(state / "recovery-policy.json", SERVICE_ID, SERVICE_ID)
        qualify_fixture(state)
        RuntimeFixture.grant_dashboard(state / "recovery-qualified.json")

    def host_status(self):
        status, _, result = self.web.api("GET", "/api/diagnostics")
        self.assertEqual(status, 200)
        return result["host"]["summary"]

    def test_server_status_reports_every_observation_the_workers_write(self):
        host = self.host_status()
        for section in ("monitoring", "external_monitoring", "backup", "certificates"):
            with self.subTest(section=section):
                self.assertNotIn(host[section].get("state"), {"unknown", "stale"})
                self.assertIsNone(host[section].get("reason"))
        self.assertTrue(host["backup"]["verified"])
        self.assertEqual(host["certificates"]["consumers"]["maddy"]["state"], "active")
        self.assertEqual(host["thresholds"], THRESHOLDS)
        self.assertGreater(host["disk_total_bytes"], 0)
        self.assertGreater(host["disk_free_bytes"], 0)

    def test_a_worker_style_atomic_rewrite_is_still_readable_and_keeps_its_sharing(self):
        path = self.web.observation_directories["alerts"] / "monitor-observation.json"
        before = path.stat()
        atomic_json(path, self.observation(outcome="rewritten"))
        after = path.stat()
        self.assertNotEqual(before.st_ino, after.st_ino)
        self.assertEqual((after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)),
                         (0, SERVICE_ID, 0o640))
        self.assertEqual(self.host_status()["monitoring"]["outcome"], "rewritten")
        atomic_json(self.web.records["manifest.json"], self.observation(verified=False))
        self.assertIs(self.host_status()["backup"]["verified"], False)

    def test_forwarding_page_data_reads_the_root_written_records(self):
        status, _, page = self.web.api("GET", "/api/forwarding")
        self.assertEqual(status, 200)
        self.assertEqual(page["connection"]["state"], "connected")
        self.assertEqual(page["worker"]["outcome"], "active")

    def test_recovery_policy_proof_and_listing_work_for_the_service_user(self):
        status, _, result = self.web.api("GET", "/api/recovery/policy")
        self.assertEqual(status, 200)
        self.assertEqual(result["policy"]["revision"], POLICY["revision"])
        status, _, result = self.web.api("GET", "/api/recovery/mailboxes")
        self.assertEqual(status, 200)
        self.assertEqual(result["mailboxes"], [])
