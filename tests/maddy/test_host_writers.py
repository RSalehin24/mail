import os
import unittest
from unittest.mock import patch

from helper.journal import Journal
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import HostLock, LockPolicy
from host.renewal import Certbot
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.acme_support import AcmeFixtureDocker
from tests.maddy_support import RealMaddyFixture
from tools.resources import ACME_WRITER_MEMORY_MIB


class HostWriterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.adapter = AcmeFixtureDocker(self.fixture, os.environ["MAIL_ADMIN_RUNTIME_REFERENCE"])
        self.addCleanup(self.adapter.close)
        state = self.fixture.root / "generation-state.json"
        atomic_json(state, {"consumers": {}})
        self.generations = Generations(
            GenerationPaths(self.fixture.root, state),
            (CertificatePolicy(os.getgid(), os.getuid()), PairValidator(), lambda: 1800000000),
        )
        self.certbot = Certbot(self.adapter, (self.generations, {
            "certbot_image": self.adapter.image,
            "transient_resources": {
                "memory_limit_bytes": ACME_WRITER_MEMORY_MIB * 1024 * 1024,
                "tmpfs_limit_bytes": 1024 * 1024,
                "cpu_limit": 0.5,
            },
        }))
        paths = [self.fixture.root / name for name in ("renewal.lock", "maintenance.lock")]
        for path in paths:
            path.touch(mode=0o600)
        self.coordination = Coordination(
            MaintenanceLocks(*paths, LockPolicy(0.1, os.getuid())),
            (Journal.provision(self.fixture.root / "coordination-journal.db"), self.generations),
        )

    def test_killed_attach_leaves_a_real_writer_that_blocks_backup_but_allows_mailbox_reads(self):
        self.fixture.start()
        self.adapter.attach_timeout = 1
        locks = self.coordination.locks
        before = [path.stat().st_ino for path in (locks.renewal, locks.maddy)]
        with HostLock(locks.renewal, locks.policy):
            with self.assertRaisesRegex(ServiceError, "acme_completion_unknown"):
                self.certbot.acquire()
        identifier = self.generations.read()["acme_run"]["container_id"]
        self.adapter.wait_for(self.adapter.storage / "started")
        self.assertTrue(self.adapter.inspect(identifier)["State"]["Running"])
        with HostLock(locks.maddy, locks.policy):
            self.assertEqual(self.fixture.maddy.inventories(), {"credentials": [], "storage": []})
        with self.assertRaisesRegex(ServiceError, "acme_completion_unknown"):
            with self.coordination.consistent():
                self.fail("A surviving writer cannot overlap backup capture")
        with self.assertRaisesRegex(ServiceError, "acme_completion_unknown"):
            self.certbot.reconcile()
        self.adapter.release(identifier)
        self.certbot.reconcile()
        with self.coordination.consistent():
            self.assertEqual(self.generations.read()["acme_run"]["exit_code"], 0)
        self.assertEqual(before, [path.stat().st_ino for path in (locks.renewal, locks.maddy)])
        record = self.adapter.observations()
        self.assertLess(int(record["memory.peak"]), ACME_WRITER_MEMORY_MIB * 1024 * 1024)
        self.assertIn("oom_kill 0", record["memory.events"])

    def test_real_created_container_is_reconciled_after_a_lost_identifier_record(self):
        original = self.generations.write

        def interrupted(record):
            if record["acme_run"]["state"] == "starting":
                raise OSError("Synthetic death before the container identifier is persisted")
            original(record)

        with patch.object(self.generations, "write", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.certbot.acquire()
        identifier = next(iter(self.adapter.created))
        self.assertEqual(self.adapter.inspect(identifier)["State"]["Status"], "created")
        self.certbot.reconcile()
        self.assertEqual(self.generations.read()["acme_run"]["state"], "not_started")
        self.assertEqual(self.adapter.created, set())
        self.assertFalse((self.adapter.storage / "started").exists())

    def test_real_nonzero_writer_exit_is_preserved_as_failure_and_not_replayed(self):
        (self.adapter.storage / "release").touch()
        (self.adapter.storage / "exit").write_text("19")
        with self.assertRaises(ServiceError):
            self.certbot.acquire()
        identifier = self.generations.read()["acme_run"]["container_id"]
        self.certbot.reconcile()
        record = self.generations.read()["acme_run"]
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["exit_code"], 19)
        self.assertEqual(self.adapter.inspect(identifier)["State"]["ExitCode"], 19)
        self.assertEqual(self.adapter.created, {identifier})
        self.assertNotIn("desired_generation", self.generations.read())
