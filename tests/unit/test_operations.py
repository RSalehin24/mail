import io
import os
import secrets
import shutil
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import HelperFixture
from tests.tls_support import CertificateFixture, openssl_executable


class RecordingConsumer:
    def __init__(self, failed=False):
        self.calls = []
        self.failed = failed

    def activate(self, generation, certificate):
        self.calls.append(generation.name)
        if self.failed:
            raise ServiceError("fixture_activation_failure", 503)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        self.certificates = CertificateFixture(self.fixture.root)
        self.validator = PairValidator(self.certificates.trust, openssl_executable())
        generation_directory = self.fixture.root / "generations"
        generation_directory.mkdir()
        state = self.fixture.root / "certificates.json"
        atomic_json(
            state,
            {
                "coordination_migrated": True,
                "consumers": {
                    "maddy": {"intent": "expected_running", "state": "pending"},
                    "mail-admin": {"intent": "never_deployed", "state": "absent"},
                }
            },
        )
        self.generations = Generations(
            GenerationPaths(generation_directory, state),
            (
                CertificatePolicy(os.getgid(), os.getuid(), self.certificates.trust),
                self.validator,
                lambda: self.fixture.now,
            ),
        )

    def test_bad_pair_wrong_hostname_expiry_and_untrusted_chain_are_rejected(self):
        valid = self.certificates.issue()
        invalid = self.certificates.issue("wrong-host", "wrong.example")
        expired = self.certificates.issue("expired", expired=True)
        for source in (invalid, expired):
            with self.assertRaises(ServiceError):
                self.validator.validate(source)
        other = self.certificates.issue("other-key")
        shutil.copy2(other / "privkey.pem", valid / "privkey.pem")
        with self.assertRaises(ServiceError):
            self.validator.validate(valid)
        untrusted = PairValidator(None, self.validator.executable)
        with self.assertRaises(ServiceError):
            untrusted.validate(other)

    def test_publication_is_immutable_and_pending_consumers_are_independent(self):
        source = self.certificates.issue()
        previous = os.umask(0o077)
        try:
            generation = self.generations.stage(source)
        finally:
            os.umask(previous)
        self.generations.publish(generation)
        self.generations.register("mail-admin")
        failing, successful = RecordingConsumer(True), RecordingConsumer()
        with self.assertRaises(ServiceError):
            self.generations.activate({"maddy": failing, "mail-admin": successful})
        self.assertEqual(len(successful.calls), 1)
        self.assertEqual(self.generations.read()["consumers"]["maddy"]["state"], "pending")
        failing.failed = False
        self.generations.activate({"maddy": failing, "mail-admin": successful})
        self.assertEqual(self.generations.read()["outcome"], "active")
        self.assertEqual(generation.stat().st_mode & 0o777, 0o750)
        self.assertEqual((generation / "privkey.pem").stat().st_mode & 0o777, 0o640)
        self.assertIn(generation.name, self.generations.referenced_generations())

    def test_never_deployed_and_maintenance_consumers_stay_stopped(self):
        generation = self.generations.stage(self.certificates.issue())
        self.generations.publish(generation)
        maddy, dashboard = RecordingConsumer(), RecordingConsumer()
        self.generations.activate({"maddy": maddy, "mail-admin": dashboard})
        self.assertEqual(dashboard.calls, [])
        self.generations.register("mail-admin")
        self.generations.maintenance("mail-admin", "offline_recovery")
        self.generations.activate({"maddy": maddy, "mail-admin": dashboard})
        self.assertEqual(dashboard.calls, [])

    def test_legacy_rollback_blocks_dashboard_registration_with_generation_mounts(self):
        state = self.generations.read()
        state["coordination_migrated"] = False
        self.generations.write(state)
        with self.assertRaisesRegex(ServiceError, "matched_renewal_cutover_required"):
            self.generations.register("mail-admin")
        self.assertEqual(
            self.generations.read()["consumers"]["mail-admin"]["intent"], "never_deployed"
        )
    def test_retention_preserves_desired_active_and_recorded_rollback_generations(self):
        source = self.certificates.issue()
        current = self.generations.stage(source)
        self.generations.publish(current)
        rollback = "a" * 64
        obsolete = "b" * 64
        for name in (rollback, obsolete):
            path = self.generations.paths.directory / name
            path.mkdir()
            os.utime(path, (self.fixture.now - 1000, self.fixture.now - 1000))
        state = self.generations.read()
        state["rollback_generations"] = [rollback]
        self.generations.write(state)
        self.generations.prune(60)
        self.assertTrue(current.exists())
        self.assertTrue((self.generations.paths.directory / rollback).exists())
        self.assertFalse((self.generations.paths.directory / obsolete).exists())

    def test_resuming_a_consumer_keeps_its_old_mount_referenced_until_activation_is_verified(self):
        previous = self.generations.stage(self.certificates.issue())
        self.generations.publish(previous)
        self.generations.register("mail-admin")
        consumers = {"maddy": RecordingConsumer(), "mail-admin": RecordingConsumer()}
        self.generations.activate(consumers)
        prior = self.generations.read()["consumers"]["mail-admin"]
        self.generations.maintenance("mail-admin", "offline_recovery")
        desired = self.generations.stage(self.certificates.issue("replacement"))
        self.generations.publish(desired)
        self.generations.activate(consumers)
        self.generations.register("mail-admin")
        resumed = self.generations.read()["consumers"]["mail-admin"]
        self.assertEqual(resumed["active_generation"], previous.name)
        self.assertEqual(resumed["served_expiry"], prior["served_expiry"])
        self.assertNotIn("reason", resumed)
        os.utime(previous, (self.fixture.now - 1000, self.fixture.now - 1000))
        self.generations.prune(60)
        self.assertTrue(previous.exists())
        self.generations.activate(consumers)
        self.assertEqual(
            self.generations.read()["consumers"]["mail-admin"]["active_generation"], desired.name
        )

    def test_locks_are_stable_bounded_and_unknown_writers_block_capture(self):
        renewal = self.fixture.root / "renewal.lock"
        renewal.touch(mode=0o600)
        policy = LockPolicy(0.03, os.getuid())
        coordination = Coordination(
            MaintenanceLocks(renewal, self.fixture.lock, policy),
            (self.fixture.journal, self.generations),
        )
        before = renewal.stat().st_ino
        with HostLock(renewal, policy):
            with self.assertRaises(ServiceError):
                with coordination.consistent():
                    self.fail("overlapping renewal lock")
        self.assertEqual(renewal.stat().st_ino, before)
        state = self.generations.read()
        state["acme_run"] = {"state": "unknown", "container_id": "fixture-survivor"}
        self.generations.write(state)
        with self.assertRaises(ServiceError):
            with coordination.consistent():
                self.fail("unknown ACME writer")

    def test_streaming_encryption_roundtrip_and_tamper_reject_before_extraction(self):
        source = self.fixture.root / "source.tar"
        body = b"fixture message body" * 100000
        with tarfile.open(source, "x:") as archive:
            member = tarfile.TarInfo("mail/message.eml")
            member.size = len(body)
            member.uid, member.gid = os.getuid(), os.getgid()
            archive.addfile(member, io.BytesIO(body))
        key = secrets.token_bytes(32)
        sealed, decrypted = self.fixture.root / "sealed", self.fixture.root / "decrypted.tar"
        encrypt_archive(source, sealed, key)
        decrypt_archive(sealed, decrypted, key)
        target = self.fixture.root / "restored"
        extract_archive(decrypted, target)
        self.assertEqual((target / "mail/message.eml").read_bytes(), body)
        self.assertEqual((target / "mail/message.eml").stat().st_uid, os.getuid())
        altered = bytearray(sealed.read_bytes())
        altered[-20] ^= 1
        sealed.write_bytes(altered)
        with self.assertRaises(ServiceError):
            decrypt_archive(sealed, self.fixture.root / "bad.tar", key)
        self.assertFalse((self.fixture.root / "bad.tar").exists())

    def test_tar_traversal_never_extracts_outside_isolated_target(self):
        source = self.fixture.root / "malicious.tar"
        with tarfile.open(source, "x:") as archive:
            member = tarfile.TarInfo("../escaped")
            member.size = 1
            archive.addfile(member, io.BytesIO(b"x"))
        with self.assertRaises(tarfile.FilterError):
            extract_archive(source, self.fixture.root / "isolated")
        self.assertFalse((self.fixture.root / "escaped").exists())

    def test_archive_ownership_uses_the_filtered_relative_path(self):
        source = self.fixture.root / "absolute-name.tar"
        with tarfile.open(source, "x:") as archive:
            member = tarfile.TarInfo("/ownership-fixture")
            member.size = 1
            member.uid, member.gid = os.getuid(), os.getgid()
            archive.addfile(member, io.BytesIO(b"x"))
        target = self.fixture.root / "safe-ownership"
        with patch("backend.archive.os.chown") as ownership:
            extract_archive(source, target)
        ownership.assert_any_call(
            target / "ownership-fixture", os.getuid(), os.getgid(), follow_symlinks=False
        )
        for call in ownership.call_args_list:
            self.assertEqual(Path(call.args[0]), target / "ownership-fixture")
        self.assertEqual((target / "ownership-fixture").read_bytes(), b"x")
