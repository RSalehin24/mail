"""Short-lived attachment spooling. No upload is a permanent mailbox copy."""

import asyncio
import base64
import binascii
import os
import quopri
import secrets
import shutil
import tempfile
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from backend.webmail_auth import MailboxAuthentication
from backend.webmail_imap import MAX_ATTACHMENTS, MAX_MESSAGE
from backend.webmail_mime import safe_filename, structure
from backend.webmail_models import MessageRef
from shared.errors import ServiceError


class MailFiles:
    def __init__(self, authentication: MailboxAuthentication, capacity_mib: int = 32) -> None:
        if not 10 <= capacity_mib <= 512:
            raise ServiceError("invalid_attachment_spool_capacity", 503)
        self.auth = authentication
        self.capacity_bytes = capacity_mib * 1024 * 1024
        self.io_capacity_bytes = self.capacity_bytes * 2
        spool = authentication.database.path.parent / ".webmail-spool"
        if spool.is_symlink():
            raise ServiceError("unsafe_attachment_spool", 503)
        spool.mkdir(mode=0o700, exist_ok=True)
        if spool.stat().st_uid != os.geteuid() or spool.stat().st_mode & 0o077:
            raise ServiceError("unsafe_attachment_spool", 503)
        # A single-worker restart invalidates every session. Remove its abandoned
        # temporary uploads before accepting new sessions. No mailbox is cached here.
        for abandoned in spool.glob("webmail-*"):
            if abandoned.is_symlink() or abandoned.stat().st_uid != os.geteuid():
                raise ServiceError("unsafe_attachment_spool", 503)
            shutil.rmtree(abandoned)
        self.temporary = tempfile.TemporaryDirectory(prefix="webmail-", dir=spool)
        self.root = Path(self.temporary.name)
        self.lock = threading.RLock()
        self.uploads: dict[str, dict[str, Any]] = {}
        self.reserved = 0
        self.upload_reserved = 0
        self.downloads: dict[Path, tuple[float, int]] = {}

    def cleanup(self) -> None:
        self.auth.cleanup()
        with self.lock:
            for key, record in list(self.uploads.items()):
                if (
                    record["session"] not in self.auth.vault
                    or record["expiry"] <= self.auth.clock()
                ):
                    Path(record["path"]).unlink(missing_ok=True)
                    del self.uploads[key]
            for path, (expiry, _) in list(self.downloads.items()):
                if expiry <= self.auth.clock():
                    self.release(path)

    async def upload(
        self, token: str, filename: str, source: AsyncIterator[bytes]
    ) -> dict[str, Any]:
        # Identity checks cross the integration network; never block the ASGI loop.
        user = await asyncio.to_thread(self.auth.require, token)
        await asyncio.to_thread(self.cleanup)
        key = secrets.token_hex(16)
        path, total = self.root / key, 0
        with self.lock:
            used = sum(r["size"] for r in self.uploads.values())
            if (
                used + self.upload_reserved + MAX_ATTACHMENTS > self.capacity_bytes
                or used + self.reserved + MAX_ATTACHMENTS > self.io_capacity_bytes
            ):
                raise ServiceError("attachment_capacity_busy", 503, 2)
            self.reserved += MAX_ATTACHMENTS
            self.upload_reserved += MAX_ATTACHMENTS
        try:
            # Writes are <=64 KiB and never queue an unbounded in-memory request.
            with path.open("xb") as output:
                os.chmod(path, 0o600)
                async for chunk in source:
                    total += len(chunk)
                    if total > MAX_ATTACHMENTS:
                        raise ServiceError("attachment_limit_exceeded", 413)
                    output.write(chunk)
            await asyncio.to_thread(self.auth.require, token)
            with self.lock:
                current = [r for r in self.uploads.values() if r["session"] == user.token_hash]
                if len(current) >= 30 or total + sum(r["size"] for r in current) > MAX_ATTACHMENTS:
                    raise ServiceError("attachment_limit_exceeded", 413)
                self.uploads[key] = {
                    "id": key,
                    "session": user.token_hash,
                    "path": path,
                    "filename": safe_filename(filename),
                    "size": total,
                    "expiry": self.auth.clock() + 3600,
                }
            return {"id": key, "filename": safe_filename(filename), "size": total}
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            with self.lock:
                self.reserved -= MAX_ATTACHMENTS
                self.upload_reserved -= MAX_ATTACHMENTS

    def get(self, token: str, keys: list[str]) -> list[dict[str, Any]]:
        self.cleanup()
        user = self.auth.require(token)
        with self.lock:
            result = []
            for key in dict.fromkeys(keys):
                record = self.uploads.get(key)
                if record is None or record["session"] != user.token_hash:
                    raise ServiceError("upload_expired", 404)
                result.append(record.copy())
            return result

    def delete(self, token: str, key: str) -> dict[str, bool]:
        record = self.get(token, [key])[0]
        with self.lock:
            Path(record["path"]).unlink(missing_ok=True)
            self.uploads.pop(key, None)
        return {"deleted": True}

    def download(self, token: str, ref: MessageRef, part_id: str) -> tuple[Path, str]:
        # Spool a single MIME section in bounded FETCH chunks. No Seen flag changes.
        encoded = self.root / ("download-" + secrets.token_hex(16))
        decoded = encoded.with_suffix(".decoded")
        reservation = 0
        retained = False
        try:
            with self.auth.connect(token) as (_, mail):
                mail.check(ref)
                part = next(
                    (
                        p
                        for p in structure(mail, ref.uid)
                        if p["part"] == part_id and p["attachment"]
                    ),
                    None,
                )
                if part is None:
                    raise ServiceError("attachment_not_found", 404)
                if part["size"] < 0 or part["size"] > MAX_MESSAGE:
                    raise ServiceError("attachment_too_large", 413)
                with self.lock:
                    used = sum(r["size"] for r in self.uploads.values())
                    needed = max(1, int(part["size"])) * 2
                    if used + self.reserved + needed > self.io_capacity_bytes:
                        raise ServiceError("attachment_capacity_busy", 503, 2)
                    reservation = needed
                    self.reserved += reservation
                offset = 0
                with encoded.open("xb") as output:
                    os.chmod(encoded, 0o600)
                    while offset < part["size"]:
                        chunk = mail.literal(ref.uid, f"BODY.PEEK[{part_id}]<{offset}.262144>")
                        if not chunk:
                            break
                        if offset + len(chunk) > MAX_MESSAGE:
                            raise ServiceError("attachment_too_large", 413)
                        output.write(chunk)
                        offset += len(chunk)
                if offset != part["size"]:
                    raise ServiceError("attachment_incomplete", 409)
            with encoded.open("rb") as source, decoded.open("xb") as output:
                os.chmod(decoded, 0o600)
                if part["encoding"] == "base64":
                    pending = b""
                    while raw := source.read(65536):
                        pending += b"".join(raw.split())
                        end = len(pending) // 4 * 4
                        output.write(base64.b64decode(pending[:end], validate=True))
                        pending = pending[end:]
                    if pending:
                        raise ServiceError("invalid_attachment_encoding", 422)
                elif part["encoding"] == "quoted-printable":
                    pending = b""
                    while raw := source.read(65536):
                        raw = pending + raw
                        keep = 2 if raw[-2:-1] == b"=" else 1 if raw[-1:] == b"=" else 0
                        output.write(quopri.decodestring(raw[:-keep] if keep else raw))
                        pending = raw[-keep:] if keep else b""
                    output.write(quopri.decodestring(pending))
                else:
                    while raw := source.read(65536):
                        output.write(raw)
            with self.lock:
                self.downloads[decoded] = (self.auth.clock() + 3600, reservation // 2)
                self.reserved -= reservation // 2
                retained = True
            return decoded, str(part["filename"])
        except (ValueError, binascii.Error) as error:
            decoded.unlink(missing_ok=True)
            raise ServiceError("invalid_attachment_encoding", 422) from error
        except BaseException:
            decoded.unlink(missing_ok=True)
            raise
        finally:
            encoded.unlink(missing_ok=True)
            if not retained:
                with self.lock:
                    self.reserved -= reservation

    def release(self, path: Path) -> None:
        with self.lock:
            path.unlink(missing_ok=True)
            record = self.downloads.pop(path, None)
            if record is not None:
                self.reserved -= record[1]

    def stream(self, path: Path) -> Iterator[bytes]:
        try:
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    yield chunk
        finally:
            self.release(path)

    def close(self) -> None:
        self.temporary.cleanup()
