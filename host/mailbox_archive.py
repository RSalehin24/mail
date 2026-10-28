"""Private encrypted mailbox recovery points with verified round trips."""

import hashlib
import os
import secrets
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from host.mailbox_store import body_path, connect, extract_mailbox, validate
from shared.archive import decrypt_archive, encrypt_archive, extract_archive
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json, sync_directory
from shared.validation import request_id


def digest(path: Path) -> str:
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


class MailboxArchives:
    def __init__(self, root: Path, key: Path, owner: int = 0) -> None:
        if root.is_symlink() or key.is_symlink():
            raise ServiceError("recovery_storage_invalid", 503)
        root.mkdir(mode=0o700, exist_ok=True)
        if root.stat().st_uid != owner or root.stat().st_mode & 0o077:
            raise ServiceError("recovery_storage_invalid", 503)
        self.root, self.owner = root, owner
        if not key.exists():
            if any(root.iterdir()):
                raise ServiceError("recovery_key_missing", 503)
            descriptor = os.open(key, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as writer:
                writer.write(secrets.token_bytes(32))
                writer.flush()
                os.fsync(writer.fileno())
            sync_directory(key.parent)
        self.key = protected_bytes(key, owner)
        if len(self.key) != 32:
            raise ServiceError("recovery_key_invalid", 503)

    def paths(self, identifier: str) -> tuple[Path, Path]:
        identifier = request_id(identifier)
        return self.root / (identifier + ".enc"), self.root / (identifier + ".json")

    def create(
        self, identifier: str, database: Path, bodies: Path, email: str, metadata: dict[str, Any]
    ) -> str:
        archive, manifest = self.paths(identifier)
        self.admit(database, bodies, email)
        if archive.exists() or manifest.exists():
            raise ServiceError("recovery_point_conflict", 409)
        with tempfile.TemporaryDirectory(prefix=".capture-", dir=self.root) as work:
            directory = Path(work)
            selected = directory / "mailbox"
            extract_mailbox(database, bodies, email, selected)
            atomic_json(selected / "metadata.json", {**metadata, "email": email})
            checksums = {
                str(p.relative_to(selected)): digest(p) for p in selected.rglob("*") if p.is_file()
            }
            plain = directory / "mailbox.tar"
            with tarfile.open(plain, "w") as writer:
                for path in sorted(selected.rglob("*")):
                    if path.is_file():
                        writer.add(path, arcname=str(path.relative_to(selected)))
            encrypt_archive(plain, archive, self.key)
            verified = directory / "verified.tar"
            decrypt_archive(archive, verified, self.key)
            if digest(verified) != digest(plain):
                raise ServiceError("recovery_point_verification_failed", 503)
            extracted = directory / "verified"
            extract_archive(verified, extracted)
            self._verify(extracted, email, checksums)
            atomic_json(
                manifest,
                {
                    "schema_version": 1,
                    "email": email,
                    "verified": True,
                    "key_sha256": hashlib.sha256(self.key).hexdigest(),
                    "encrypted_sha256": digest(archive),
                    "files": checksums,
                },
            )
            sync_directory(self.root)
        return identifier

    def admit(self, database: Path, bodies: Path, email: str) -> None:
        """Conservative bounded-work admission, before stopping the live writer.

        At most 32 MiB/2000 bodies: eight full I/O/crypto passes at a conservative
        1 MiB/s plus 150s lifecycle reserve fit the worker's 480s mutation budget.
        """
        with connect(database) as connection:
            account = connection.execute('SELECT id FROM users WHERE username=?',
                                         (email,)).fetchone()
            if account is None:
                raise ServiceError('account_not_found', 404)
            keys = [row[0] for row in connection.execute(
                'SELECT id FROM extKeys WHERE uid=?', (account[0],),
            )]
        total = database.stat().st_size + sum(body_path(bodies, key).stat().st_size for key in keys)
        if total > 32 * 1024**2 or len(keys) > 2000:
            raise ServiceError('recovery_operation_budget_exceeded', 409)
        disk = os.statvfs(self.root)
        if disk.f_bavail * disk.f_frsize - 8 * total < 1024**3:
            raise ServiceError('insufficient_recovery_space', 409)

    def available(self, identifier: str, email: str) -> bool:
        # The protected manifest attests the full verified round trip at capture.
        # Check the current key and every encrypted byte without repeatedly
        # extracting message bodies inside a helper/listing request.
        try:
            archive, manifest = self.paths(identifier)
            record = protected_json(manifest, self.owner)
            return bool(record.get('verified') is True and record.get('email') == email
                        and not archive.is_symlink()
                        and record.get('key_sha256') == hashlib.sha256(self.key).hexdigest()
                        and digest(archive) == record['encrypted_sha256'])
        except (OSError, ValueError, KeyError, ServiceError):
            return False

    def _verify(self, directory: Path, email: str, checksums: dict[str, str]) -> None:
        actual = {
            str(p.relative_to(directory)): digest(p)
            for p in directory.rglob("*")
            if p.is_file() and not p.is_symlink()
        }
        if actual != checksums:
            raise ServiceError("recovery_point_verification_failed", 503)
        with connect(directory / "imapsql.db") as connection:
            validate(connection)
            if [row[0] for row in connection.execute("SELECT username FROM users")] != [email]:
                raise ServiceError("recovery_point_identity_mismatch", 503)

    @contextmanager
    def open(self, identifier: str, email: str) -> Iterator[Path]:
        archive, manifest = self.paths(identifier)
        record = protected_json(manifest, self.owner)
        if (
            record.get("verified") is not True
            or record.get("email") != email
            or archive.is_symlink()
            or digest(archive) != record["encrypted_sha256"]
        ):
            raise ServiceError("recovery_point_unavailable", 409)
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=self.root) as work:
            directory = Path(work)
            decrypt_archive(archive, directory / "mailbox.tar", self.key)
            extract_archive(directory / "mailbox.tar", directory / "mailbox")
            self._verify(directory / "mailbox", email, record["files"])
            yield directory / "mailbox"

    def remove(self, identifier: str) -> None:
        # The worker durably records removal intent before calling this method.
        for path in self.paths(identifier):
            if path.is_symlink():
                raise ServiceError("recovery_storage_invalid", 503)
            path.unlink(missing_ok=True)
        sync_directory(self.root)
