import concurrent.futures
import os
import sqlite3
import unittest

from backend.budgets import BudgetReservation, Budgets
from backend.database import Database
from backend.provisioning import InstallationPaths, create_installation
from backend.retention import cleanup
from shared.errors import ServiceError
from shared.files import protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME
from tests.support import StateFixture


class StateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)

    def test_missing_wrong_marker_and_wrong_schema_fail_closed(self):
        with self.assertRaises(ServiceError):
            Database(self.fixture.root / "missing.db", "fixture-installation")
        self.assertFalse((self.fixture.root / "missing.db").exists())
        with self.assertRaises(ServiceError):
            Database(self.fixture.database.path, "other-installation")
        with sqlite3.connect(self.fixture.database.path) as connection:
            connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ServiceError):
            Database(self.fixture.database.path, "fixture-installation")

    def test_busy_and_corrupt_storage_fail_explicitly(self):
        with sqlite3.connect(self.fixture.database.path) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with self.assertRaises(ServiceError) as caught:
                with self.fixture.database.transaction():
                    self.fail("must not obtain a busy transaction")
            self.assertEqual(caught.exception.status, 503)
        corrupt = self.fixture.root / "corrupt.db"
        corrupt.write_bytes(b"not a SQLite database")
        with self.assertRaises(ServiceError):
            Database(corrupt, "fixture")

    def test_atomic_rate_budget_boundary_and_restart(self):
        budgets = Budgets(self.fixture.database)
        for _ in range(15):
            budgets.reserve(BudgetReservation("auth", "peer-a", self.fixture.now))
        restarted = Budgets(Database(self.fixture.database.path, "fixture-installation"))
        with self.assertRaises(ServiceError) as caught:
            restarted.reserve(BudgetReservation("auth", "peer-a", self.fixture.now))
        self.assertEqual((caught.exception.status, caught.exception.retry_after), (429, 900))
        for _ in range(15):
            restarted.reserve(BudgetReservation("auth", "peer-b", self.fixture.now))
        with self.assertRaises(ServiceError):
            restarted.reserve(BudgetReservation("auth", "peer-c", self.fixture.now + 0.1))
        restarted.reserve(BudgetReservation("auth", "peer-a", self.fixture.now + 900))

    def test_parallel_reservations_never_exceed_send_limit(self):
        def reserve(_):
            try:
                Budgets(self.fixture.database).reserve(
                    BudgetReservation("send", "same-peer", self.fixture.now)
                )
                return True
            except ServiceError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(reserve, range(20))), 3)

    def test_authenticated_encryption_rejects_tampering_wrong_key_and_purpose(self):
        keys = self.fixture.keys
        sealed = keys.seal("private fixture", "totp")
        self.assertEqual(keys.open(sealed, "totp"), "private fixture")
        for value, purpose in ((sealed, "relay"), (sealed[:-5] + "AAAAA", "totp")):
            with self.assertRaises(ServiceError):
                keys.open(value, purpose)

    def test_provision_once_and_snapshot(self):
        key_directory = self.fixture.root / "keys"
        key_directory.mkdir(mode=0o700)
        paths = InstallationPaths(
            self.fixture.root / "fresh.db", key_directory, self.fixture.root / "marker.json"
        )
        database, keys = create_installation(paths)
        marker = protected_json(paths.marker, os.getuid())
        self.assertEqual(marker["installation_id"], database.identity)
        self.assertEqual(len(keys.encryption), 32)
        policy = protected_json(paths.secrets / RUNTIME_POLICY_NAME, os.getuid())
        self.assertEqual(
            policy, {"installation_id": database.identity, "intent": "expected_running"}
        )
        with self.assertRaises(ServiceError):
            create_installation(paths)
        destination = self.fixture.root / "snapshot.db"
        database.snapshot(destination)
        Database(destination, database.identity)

    def test_orphaned_runtime_policy_prevents_partial_installation_from_being_recreated(self):
        directory = self.fixture.root / "orphaned-keys"
        directory.mkdir()
        (directory / RUNTIME_POLICY_NAME).write_text("orphaned installation state")
        paths = InstallationPaths(
            self.fixture.root / "not-created.db", directory, self.fixture.root / "not-created.json"
        )
        with self.assertRaisesRegex(ServiceError, "installation_already_exists"):
            create_installation(paths)
        self.assertFalse(paths.database.exists())
        self.assertFalse((directory / "encryption.key").exists())

    def test_duplicate_root_record_fields_are_rejected(self):
        path = self.fixture.root / "duplicate.json"
        path.write_text('{"enabled":false,"enabled":true}')
        path.chmod(0o600)
        with self.assertRaises(ServiceError):
            protected_json(path, os.getuid())

    def test_non_finite_host_policy_and_timestamp_numbers_fail_closed(self):
        path = self.fixture.root / "invalid-number.json"
        for literal in ("NaN", "Infinity", "-Infinity", "1e10000"):
            path.write_text('{"valid_until":' + literal + "}")
            path.chmod(0o600)
            with self.assertRaises(ServiceError) as caught:
                protected_json(path, os.getuid())
            self.assertEqual(caught.exception.code, "invalid_host_record")

    def test_cleanup_retains_active_limits_and_watermark(self):
        with self.fixture.database.transaction() as connection:
            connection.execute("UPDATE admin_auth SET last_step=123")
            connection.executemany(
                "INSERT INTO auth_rate_limits(bucket,peer,reserved_at) VALUES('auth','peer',?)",
                [(self.fixture.now - 899,), (self.fixture.now - 900,)],
            )
        cleanup(self.fixture.database, self.fixture.now)
        with self.fixture.database.transaction() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM auth_rate_limits").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT last_step FROM admin_auth").fetchone()[0], 123
            )
