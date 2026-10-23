import hashlib
import os
import shutil
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from host.alerts import Alerts
from host.backup import Backup, BackupDependencies, BackupPaths
from host.locks import LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.support import StateFixture


class SnapshotFixture:
    journal = None

    def __init__(self):
        self.active = ["maddy"]
        self.events = []
        self.locked = False

    @contextmanager
    def consistent(self):
        self.locked = True
        self.events.append("renewal_then_maddy_locked")
        try:
            yield
        finally:
            self.locked = False
            self.events.append("unlocked")

    def inventories(self):
        return {"credentials": ["fixture@rsalehin24.me"], "storage": ["fixture@rsalehin24.me"]}

    def running(self):
        return list(self.active)

    def pause(self, names):
        self.active = [name for name in self.active if name not in names]
        self.events.append("stopped")

    def resume(self, names):
        self.active.extend(names)
        self.events.append("resumed")


class HostCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)

    def test_capture_stops_writers_transfer_runs_after_resume_and_publication_follows_verification(
        self,
    ):
        services = SnapshotFixture()
        source, staging, remote = [
            self.fixture.root / name for name in ("source", "staging", "remote")
        ]
        for path in (source, staging, remote):
            path.mkdir(mode=0o700)
        (source / "fixture-message").write_bytes(b"fixture message body")

        class Encryption:
            def encrypt(inner, raw, sealed):
                self.assertFalse(services.locked)
                encrypt_archive(raw, sealed, self.fixture.keys.encryption)

        class Transfer:
            def publish(inner, archive):
                self.assertEqual(services.active, ["maddy"])
                target = remote / archive.name
                shutil.copy2(archive, target)
                return str(target)

            def fetch(inner, reference, destination):
                shutil.copy2(reference, destination)

        manifest = self.fixture.root / "manifest.json"
        backup = Backup(
            BackupPaths(staging, manifest, {"maddy": source}),
            BackupDependencies(
                services, services, Encryption(), Transfer(), lambda: self.fixture.now
            ),
        )
        original_copy = shutil.copytree

        def verify_copy(*arguments, **keywords):
            self.assertEqual(services.active, [])
            self.assertTrue(services.locked)
            return original_copy(*arguments, **keywords)

        with patch("host.backup.shutil.copytree", side_effect=verify_copy):
            snapshot = backup.capture()
        self.assertFalse(manifest.exists())
        backup.transfer(snapshot)
        record = protected_json(manifest, os.getuid())
        self.assertTrue(record["verified"])
        self.assertFalse(record["restore_passed"])
        self.assertEqual(
            services.events, ["renewal_then_maddy_locked", "stopped", "resumed", "unlocked"]
        )
        compressed = snapshot.with_suffix(".tar.gz")
        self.assertEqual(compressed.read_bytes()[:2], b"\x1f\x8b")
        restored_archive = self.fixture.root / "restored.tar.gz"
        decrypt_archive(
            remote / (snapshot.name + ".encrypted"),
            restored_archive,
            self.fixture.keys.encryption,
        )
        restored = self.fixture.root / "restored"
        extract_archive(restored_archive, restored)
        self.assertEqual(
            (restored / "snapshot/maddy/fixture-message").read_bytes(), b"fixture message body"
        )

    def test_notification_retry_deduplication_and_recovery_without_dashboard(self):
        state = self.fixture.root / "alerts.json"
        atomic_json(state, {})
        state.with_suffix(".lock").touch(mode=0o600)
        delivered = []

        class Transport:
            fail = True

            def deliver(inner, event):
                if inner.fail:
                    raise ServiceError("fixture_delivery_failed", 503)
                delivered.append(event)

        transport = Transport()
        with patch(
            "host.alerts.protected_json", side_effect=lambda path: protected_json(path, os.getuid())
        ):
            alerts = Alerts(
                state, (transport, lambda: self.fixture.now), LockPolicy(1, os.getuid())
            )
            alerts.observe("backup", "failed")
            with self.assertRaises(ServiceError):
                alerts.flush()
            alerts.observe("backup", "recovered")
            transport.fail = False
            self.fixture.now += 61
            alerts.flush()
            alerts.observe("backup", "recovered")
            alerts.flush()
            self.assertEqual([event["condition"] for event in delivered], ["failed", "recovered"])

    def test_capture_refuses_sources_that_include_or_live_inside_previous_backup_staging(self):
        staging = self.fixture.root / "staging"
        staging.mkdir()
        old = staging / "older-backup"
        old.mkdir()
        for source in (self.fixture.root, staging, old):
            services = SnapshotFixture()
            backup = Backup(
                BackupPaths(staging, self.fixture.root / "manifest.json", {"maddy": source}),
                BackupDependencies(services, services, None, None, lambda: self.fixture.now),
            )
            with self.subTest(source=source), self.assertRaisesRegex(
                ServiceError, "backup_source_overlaps_staging"
            ):
                backup.capture()
            self.assertEqual(services.events, [])
            self.assertEqual(list(staging.iterdir()), [old])

    def test_repeated_remote_verification_does_not_accumulate_downloads_even_after_tampering(self):
        staging, manifest = self.fixture.root / "verification", self.fixture.root / "manifest.json"
        staging.mkdir()
        expected = b"synthetic encrypted archive bytes"
        record = {
            "reference": "fixture-archive",
            "archive_sha256": hashlib.sha256(expected).hexdigest(),
        }
        atomic_json(manifest, record)
        transfer = SimpleNamespace(fetch=lambda reference, path: path.write_bytes(expected))
        backup = Backup(
            BackupPaths(staging, manifest, {}),
            BackupDependencies(None, None, None, transfer, lambda: self.fixture.now),
        )
        with patch(
            "host.backup.protected_json", side_effect=lambda path: protected_json(path, os.getuid())
        ):
            for _ in range(2):
                self.assertTrue(backup.verify_latest()["available"])
                self.assertEqual(list(staging.iterdir()), [])
            transfer.fetch = lambda reference, path: path.write_bytes(b"tampered fixture archive")
            with self.assertRaisesRegex(ServiceError, "off_host_archive_integrity_failed"):
                backup.verify_latest()
        self.assertEqual(list(staging.iterdir()), [])
        self.assertFalse(protected_json(manifest, os.getuid())["available"])
