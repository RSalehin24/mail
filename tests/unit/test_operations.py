import io
import os
import secrets
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from host.coordination import Coordination, MaintenanceLocks
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from tests.helper_support import HelperFixture


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)

    def test_the_lock_is_stable_and_bounded_and_an_unresolved_writer_blocks_capture(self):
        policy = LockPolicy(0.03, os.getuid())
        locks = MaintenanceLocks(self.fixture.lock, policy)
        coordination = Coordination(locks, self.fixture.journal)
        before = self.fixture.lock.stat().st_ino
        with HostLock(self.fixture.lock, policy):
            with self.assertRaises(ServiceError):
                with coordination.consistent():
                    self.fail("overlapping maintenance lock")
        self.assertEqual(self.fixture.lock.stat().st_ino, before)
        with coordination.consistent():
            pass
        with patch.object(self.fixture.journal, "unresolved", return_value=[{"id": "writer"}]):
            with self.assertRaises(ServiceError):
                with coordination.consistent():
                    self.fail("unknown writer")

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
