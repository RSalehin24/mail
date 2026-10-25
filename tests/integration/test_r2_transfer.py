import hashlib
import http.client
import secrets
import ssl
import tarfile
import time
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from host.backup import Backup, BackupDependencies, BackupPaths
from host.r2 import R2Options, R2Transfer
from host.s3_http import S3Http
from host.s3_signing import S3Credentials, SigV4
from shared.errors import ServiceError
from tests.r2_support import R2Peer
from tests.restore_support import FixtureCipher
from tests.tls_support import CertificateFixture
from tests.unit.test_host_capture_alerts import SnapshotFixture


class R2TransferTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="mail-admin-r2-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.certificates = CertificateFixture(self.root)
        self.peer = R2Peer(self.certificates.issue())
        self.addCleanup(self.peer.close)
        context = ssl.create_default_context(cafile=str(self.certificates.trust))
        self.options = R2Options("fixture-r2", "fixture-bucket", "mail-admin/", 4096, 16384, 86400)
        signer = SigV4(S3Credentials("syntheticaccessid", "synthetic-secret" * 4), ("auto", "s3"))
        self.client = S3Http(signer, lambda timeout: http.client.HTTPSConnection(
            "127.0.0.1", self.peer.server.server_port, timeout=timeout, context=context,
        ))
        self.transfer = R2Transfer(self.options, self.client)

    def archive(self, body=b"synthetic encrypted bytes"):
        path = self.root / (str(uuid.uuid4()) + ".encrypted")
        path.write_bytes(body)
        path.chmod(0o600)
        return path

    def test_compressed_encrypted_upload_download_and_authenticated_restore(self):
        message = self.root / "synthetic-message.eml"
        message.write_bytes(b"Subject: fixture\n\nOnly synthetic mail.\n" * 1000)
        raw = self.root / "backup.tar.gz"
        with tarfile.open(raw, "x:gz") as archive:
            archive.add(message, "message.eml")
        encrypted = self.root / (str(uuid.uuid4()) + ".encrypted")
        key = secrets.token_bytes(32)
        encrypt_archive(raw, encrypted, key)
        reference = self.transfer.publish(encrypted)
        downloaded = self.root / "downloaded.encrypted"
        self.transfer.fetch(reference, downloaded)
        self.assertEqual(hashlib.sha256(downloaded.read_bytes()).digest(),
                         hashlib.sha256(encrypted.read_bytes()).digest())
        self.assertEqual(downloaded.stat().st_mode & 0o777, 0o600)
        restored_archive = self.root / "restored.tar.gz"
        decrypt_archive(downloaded, restored_archive, key)
        restored = self.root / "restored"
        extract_archive(restored_archive, restored)
        self.assertEqual((restored / "message.eml").read_bytes(), message.read_bytes())
        headers = {key.lower(): value for key, value in self.peer.server.upload_headers[0].items()}
        self.assertEqual(headers["x-amz-storage-class"], "STANDARD")
        self.assertIn("/auto/s3/aws4_request", headers["authorization"])
        self.assertNotIn(b"Only synthetic mail", next(iter(self.peer.server.objects.values()))[0])

    def test_existing_archive_cannot_be_overwritten(self):
        path = self.archive()
        reference = self.transfer.publish(path)
        path.write_bytes(b"different bytes")
        with self.assertRaisesRegex(ServiceError, "off_host_archive_already_exists"):
            self.transfer.publish(path)
        restored = self.root / "unchanged"
        self.transfer.fetch(reference, restored)
        self.assertEqual(restored.read_bytes(), b"synthetic encrypted bytes")

    def test_archive_and_whole_bucket_storage_budgets_refuse_before_upload(self):
        for existing, archive_size, code in (
            (0, 4097, "r2_archive_budget_exceeded"),
            (16384, 1, "r2_storage_budget_exceeded"),
        ):
            self.peer.server.objects = {"/fixture-bucket/unrelated": (b"x" * existing,
                                                                      datetime.now(UTC))}
            with self.subTest(code=code), self.assertRaisesRegex(ServiceError, code):
                self.transfer.publish(self.archive(b"x" * archive_size))
        self.assertFalse(any(method == "PUT" for method, _ in self.peer.server.requests))

    def test_foreign_or_path_like_references_and_symlinks_are_rejected(self):
        for reference in (
            "s3://other-bucket/mail-admin/" + str(uuid.uuid4()) + ".encrypted",
            "s3://fixture-bucket/mail-admin/../outside.encrypted",
            "https://other.example/archive", "s3://fixture-bucket/other/" + self.archive().name,
        ):
            with self.subTest(reference=reference), self.assertRaises(ServiceError):
                self.transfer.fetch(reference, self.root / "download")
        path = self.archive()
        link = self.root / (str(uuid.uuid4()) + ".encrypted")
        link.symlink_to(path)
        with self.assertRaises(OSError):
            self.transfer.publish(link)
        self.assertEqual(self.peer.server.requests, [])

    def test_retention_keeps_current_rollback_and_unrecognized_objects(self):
        old, current, rollback = self.archive(), self.archive(), self.archive()
        for path in (old, current, rollback):
            self.transfer.publish(path)
        past = datetime.now(UTC) - timedelta(days=2)
        self.peer.server.objects = {
            key: (body, past) for key, (body, _modified) in self.peer.server.objects.items()
        }
        for key in ("/fixture-bucket/unrelated", "/fixture-bucket/mail-admin/operator-note"):
            self.peer.server.objects[key] = (b"keep", past)
        self.transfer.prune((datetime.now(UTC).timestamp(), {current.stem, rollback.stem}))
        keys = self.peer.server.objects
        self.assertNotIn("/fixture-bucket/mail-admin/" + old.name, keys)
        for name in (current.name, rollback.name, "operator-note"):
            self.assertIn("/fixture-bucket/mail-admin/" + name, keys)
        self.assertIn("/fixture-bucket/unrelated", keys)

    def test_denied_redirected_malformed_or_oversized_responses_never_authorize_upload(self):
        for status in (301, 403, 500):
            self.peer.server.error_status = status
            with self.subTest(status=status), self.assertRaises(ServiceError):
                self.transfer.publish(self.archive())
        self.peer.server.error_status = 0
        for body in (b"malformed", b"x" * (2 * 1024 * 1024 + 1)):
            self.peer.server.listing_body = body
            with self.assertRaises(ServiceError):
                self.transfer.publish(self.archive())
        self.assertFalse(any(method == "PUT" for method, _ in self.peer.server.requests))

    def test_partial_download_is_removed_and_existing_local_files_are_preserved(self):
        reference = self.transfer.publish(self.archive())
        destination = self.root / "download"
        self.peer.server.extra_content_length = 1
        with self.assertRaisesRegex(ServiceError, "incomplete_r2_archive"):
            self.transfer.fetch(reference, destination)
        self.assertFalse(destination.exists())
        self.peer.server.extra_content_length = 0
        destination.write_bytes(b"existing")
        with self.assertRaisesRegex(ServiceError, "archive_target_exists"):
            self.transfer.fetch(reference, destination)
        self.assertEqual(destination.read_bytes(), b"existing")

    def test_untrusted_tls_is_rejected(self):
        self.client.connect = lambda timeout: http.client.HTTPSConnection(
            "127.0.0.1", self.peer.server.server_port, timeout=timeout,
            context=ssl.create_default_context(),
        )
        with self.assertRaisesRegex(ServiceError, "off_host_transfer_failed"):
            self.transfer.publish(self.archive())
        self.assertEqual(self.peer.server.requests, [])

    def captured_backup(self, transfer):
        source, staging = self.root / 'live-mail', self.root / 'immutable-staging'
        source.mkdir(mode=0o700, exist_ok=True)
        staging.mkdir(mode=0o700, exist_ok=True)
        (source / 'message').write_bytes(b'captured-before-writers-resume')
        writers = SnapshotFixture()
        cipher = FixtureCipher()
        paths = BackupPaths(staging, self.root / 'verified-manifest.json', {'maddy': source})
        backup = Backup(paths, BackupDependencies(writers, writers, cipher, transfer, time.time))
        snapshot = backup.capture()
        (source / 'message').write_bytes(b'newer-live-mail-after-capture')
        self.assertEqual((snapshot / 'maddy/message').read_bytes(),
                         b'captured-before-writers-resume')
        self.assertFalse(backup.paths.manifest.exists())
        return backup, snapshot, cipher

    def test_immutable_staging_and_manifest_publication_follow_actual_verified_https_transfer(self):
        def fetch(reference, destination):
            self.assertFalse(backup.paths.manifest.exists())
            self.transfer.fetch(reference, destination)

        observed = SimpleNamespace(publish=self.transfer.publish, fetch=fetch)
        backup, snapshot, cipher = self.captured_backup(observed)
        manifest = backup.transfer(snapshot)
        self.assertTrue(manifest['verified'])
        self.assertTrue(backup.paths.manifest.exists())
        downloaded, raw = self.root / 'off-server.encrypted', self.root / 'restored.tar.gz'
        self.transfer.fetch(manifest['reference'], downloaded)
        decrypt_archive(downloaded, raw, cipher.key)
        restored = self.root / 'immutable-restore'
        extract_archive(raw, restored)
        self.assertEqual((restored / 'snapshot/maddy/message').read_bytes(),
                         b'captured-before-writers-resume')

    def test_failed_download_verification_never_publishes_a_trusted_manifest(self):
        def corrupted(reference, destination):
            self.transfer.fetch(reference, destination)
            destination.write_bytes(b'observed-corrupt-off-server-copy')

        observed = SimpleNamespace(publish=self.transfer.publish, fetch=corrupted)
        backup, snapshot, _ = self.captured_backup(observed)
        with self.assertRaisesRegex(ServiceError, 'off_host_archive_integrity_failed'):
            backup.transfer(snapshot)
        self.assertFalse(backup.paths.manifest.exists())
        self.assertTrue(any(method == 'PUT' for method, _ in self.peer.server.requests))
        self.assertTrue(any(method == 'GET' for method, _ in self.peer.server.requests))
