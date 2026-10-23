"""Streaming authenticated backup encryption; extraction follows complete authentication."""

import os
import secrets
import tarfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from shared.errors import ServiceError
from shared.files import sync_directory

MAGIC = b"MAIL-ADMIN-BACKUP-V1\0"
CHUNK_BYTES = 1024 * 1024


def encrypt_archive(source: Path, destination: Path, key: bytes) -> None:
    if len(key) != 32:
        raise ServiceError("invalid_archive_key", 400)
    nonce = secrets.token_bytes(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(MAGIC)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with source.open("rb") as reader, os.fdopen(descriptor, "wb") as writer:
        writer.write(MAGIC + nonce)
        for chunk in iter(lambda: reader.read(CHUNK_BYTES), b""):
            writer.write(cipher.update(chunk))
        writer.write(cipher.finalize() + cipher.tag)
        writer.flush()
        os.fsync(writer.fileno())
    sync_directory(destination.parent)


def decrypt_archive(source: Path, destination: Path, key: bytes) -> None:
    with source.open("rb") as reader:
        if (
            source.stat().st_size < len(MAGIC) + 28
            or reader.read(len(MAGIC)) != MAGIC
            or len(key) != 32
        ):
            raise ServiceError("invalid_archive", 400)
        nonce = reader.read(12)
        reader.seek(-16, os.SEEK_END)
        tag, end = reader.read(16), reader.tell() - 16
        reader.seek(len(MAGIC) + 12)
        cipher = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        cipher.authenticate_additional_data(MAGIC)
        temporary = destination.with_name(destination.name + ".unauthenticated")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as writer:
                while reader.tell() < end:
                    writer.write(cipher.update(reader.read(min(CHUNK_BYTES, end - reader.tell()))))
                writer.write(cipher.finalize())
                writer.flush()
                os.fsync(writer.fileno())
        except InvalidTag as error:
            temporary.unlink()
            raise ServiceError("archive_authentication_failed", 403) from error
    if destination.exists():
        raise ServiceError("restore_target_exists", 409)
    os.rename(temporary, destination)
    sync_directory(destination.parent)


def extract_archive(source: Path, destination: Path) -> None:
    if not hasattr(tarfile, "data_filter"):
        raise ServiceError("safe_tar_runtime_required", 503)
    if destination.exists():
        raise ServiceError("restore_target_exists", 409)
    destination.mkdir(mode=0o700)
    with tarfile.open(source, "r|*") as archive:
        for member in archive:
            if member.islnk():
                raise ServiceError("archive_hardlink_not_supported", 400)
            filtered = tarfile.data_filter(member, str(destination))
            if filtered is None:
                raise ServiceError("unsafe_archive_member", 400)
            archive.extract(filtered, destination, filter="data")
            # Restore the authenticated archive's service UID after safe path filtering.
            os.chown(destination / filtered.name, member.uid, member.gid, follow_symlinks=False)
