"""Private, append-only R2 archives with bounded bucket accounting and retention."""

import hashlib
import http.client
import io
import os
import re
import ssl
import stat
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit
from xml.etree import ElementTree

from host.s3_http import S3Http, S3Response
from host.s3_signing import S3Credentials, S3Request, SigV4, canonical_query
from shared.errors import ServiceError
from shared.files import protected_json, sync_directory

XML_NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"
MAX_LIST_BYTES = 2 * 1024 * 1024
MAX_LIST_PAGES = 10
MAX_BUDGET_BYTES = 9_000_000_000
ARCHIVE_NAME = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.encrypted"
)


@dataclass(frozen=True)
class R2Options:
    host: str
    bucket: str
    prefix: str
    max_archive_bytes: int
    max_bucket_bytes: int
    retention_seconds: int

    @classmethod
    def load(cls, configuration: dict[str, Any]) -> "R2Options":
        try:
            destination = urlsplit(configuration["inputs"]["backup_destination"])
            options = configuration["backup_r2"]
            endpoint = urlsplit(options["endpoint"])
            if not isinstance(options["credentials_path"], str) or not Path(
                options["credentials_path"]
            ).is_absolute():
                raise ValueError("A private credentials file is required")
            _require_destination(destination)
            _require_endpoint(endpoint)
            values = tuple(options[key] for key in (
                "max_archive_bytes", "max_bucket_bytes", "retention_seconds",
            ))
            if any(type(value) is not int or value <= 0 for value in values):
                raise ValueError("Positive limits required")
            if not values[0] <= values[1] <= MAX_BUDGET_BYTES:
                raise ValueError("Backup storage budget exceeded")
            return cls(endpoint.netloc, destination.netloc, destination.path.strip("/") + "/",
                       *values)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ServiceError("invalid_r2_configuration", 503) from error


def _require_destination(destination: Any) -> None:
    if (
        destination.scheme != "s3" or destination.query or destination.fragment
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", destination.netloc)
        or not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", destination.path)
    ):
        raise ValueError("A dedicated bucket prefix is required")


def _require_endpoint(endpoint: Any) -> None:
    if (
        endpoint.scheme != "https" or endpoint.path not in {"", "/"}
        or endpoint.query or endpoint.fragment
        or not re.fullmatch(
            r"[0-9a-f]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com", endpoint.netloc
        )
    ):
        raise ValueError("A verified R2 S3 API endpoint is required")


@dataclass(frozen=True)
class StoredObject:
    key: str
    size: int
    modified: float


def parse_listing(body: bytes) -> tuple[list[StoredObject], str]:
    try:
        body.decode("utf-8")
        if b"\0" in body or b"<!DOCTYPE" in body or b"<!ENTITY" in body:
            raise ValueError("XML entities are not supported")
        root = ElementTree.fromstring(body)  # noqa: S314 -- bounded, entity-free S3 XML.
        if root.tag != XML_NAMESPACE + "ListBucketResult":
            raise ValueError("Unknown S3 response")
        records = [_stored_object(item) for item in root.findall(XML_NAMESPACE + "Contents")]
        truncated = root.findtext(XML_NAMESPACE + "IsTruncated")
        token = root.findtext(XML_NAMESPACE + "NextContinuationToken") or ""
        if truncated not in {"true", "false"} or (truncated == "true" and not token):
            raise ValueError("Incomplete S3 listing")
        if len(records) > 1000 or len(token) > 4096:
            raise ValueError("Oversized S3 listing")
        return records, token if truncated == "true" else ""
    except (ElementTree.ParseError, ValueError, TypeError, OverflowError) as error:
        raise ServiceError("invalid_r2_listing", 503) from error


def _stored_object(item: ElementTree.Element) -> StoredObject:
    key = item.findtext(XML_NAMESPACE + "Key") or ""
    size = int(item.findtext(XML_NAMESPACE + "Size") or "-1")
    modified = datetime.fromisoformat(item.findtext(XML_NAMESPACE + "LastModified") or "")
    if not key or len(key.encode()) > 1024 or size < 0 or modified.tzinfo is None:
        raise ValueError("Invalid object metadata")
    return StoredObject(key, size, modified.timestamp())


class R2Transfer:
    def __init__(self, options: R2Options, client: S3Http) -> None:
        self.options, self.client = options, client

    def _request(self, method: str, key: str = "") -> S3Request:
        return S3Request(method, "/" + self.options.bucket + "/" + key,
                         {"host": self.options.host})

    def objects(self, prefix: str = "") -> list[StoredObject]:
        records: dict[str, StoredObject] = {}
        token, seen = "", set()
        for _ in range(MAX_LIST_PAGES):
            page, token = self._page((prefix, token))
            self._add_page(records, (prefix, page))
            if not token:
                return list(records.values())
            if token in seen:
                raise ServiceError("invalid_r2_listing", 503)
            seen.add(token)
        raise ServiceError("r2_listing_limit_exceeded", 503)

    @staticmethod
    def _add_page(records: dict[str, StoredObject], page: tuple[str, list[StoredObject]]) -> None:
        prefix, objects = page
        for item in objects:
            if item.key in records or not item.key.startswith(prefix):
                raise ServiceError("invalid_r2_listing", 503)
            records[item.key] = item

    def _page(self, cursor: tuple[str, str]) -> tuple[list[StoredObject], str]:
        prefix, token = cursor
        parameters = {"list-type": "2", "max-keys": "1000", "prefix": prefix}
        if token:
            parameters["continuation-token"] = token
        request = replace(self._request("GET"), query=canonical_query(parameters))
        with self.client.open(request) as response:
            response.require_status(200)
            body = io.BytesIO()
            response.copy_to(body, MAX_LIST_BYTES)
        return parse_listing(body.getvalue())

    def publish(self, archive: Path) -> str:
        if not ARCHIVE_NAME.fullmatch(archive.name):
            raise ServiceError("invalid_backup_archive_name", 503)
        descriptor = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as reader:
            metadata = os.fstat(reader.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
                raise ServiceError("invalid_backup_archive", 503)
            if metadata.st_size > self.options.max_archive_bytes:
                raise ServiceError("r2_archive_budget_exceeded", 503)
            if sum(item.size for item in self.objects()) + metadata.st_size > (
                self.options.max_bucket_bytes
            ):
                raise ServiceError("r2_storage_budget_exceeded", 503)
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
            reader.seek(0)
            self._upload(archive.name, (reader, metadata.st_size, digest))
        return self._reference(archive.name)

    def _upload(self, name: str, payload: tuple[BinaryIO, int, str]) -> None:
        reader, length, digest = payload
        request = self._request("PUT", self.options.prefix + name)
        headers = {**request.headers, "content-length": str(length),
                   "if-none-match": "*", "x-amz-storage-class": "STANDARD"}
        request = replace(request, headers=headers, payload_sha256=digest)
        with self.client.open(request, reader) as response:
            response.require_status(200)

    def _reference(self, name: str) -> str:
        return "s3://" + self.options.bucket + "/" + self.options.prefix + name

    def fetch(self, reference: str, destination: Path) -> None:
        name = reference.removeprefix(self._reference(""))
        if reference != self._reference(name) or not ARCHIVE_NAME.fullmatch(name):
            raise ServiceError("untrusted_archive_reference", 503)
        with self.client.open(self._request("GET", self.options.prefix + name)) as response:
            response.require_status(200)
            try:
                length = int(response.response.getheader("Content-Length", "-1"))
            except ValueError as error:
                raise ServiceError("invalid_r2_archive_length", 503) from error
            if not 0 < length <= self.options.max_archive_bytes:
                raise ServiceError("invalid_r2_archive_length", 503)
            self._download(response, (destination, length))

    @staticmethod
    def _download(response: S3Response, target: tuple[Path, int]) -> None:
        destination, length = target
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise ServiceError("archive_target_exists", 409) from error
        try:
            with os.fdopen(descriptor, "wb") as writer:
                response.copy_to(writer, length)
                require_download_size(writer.tell(), length)
                writer.flush()
                os.fsync(writer.fileno())
            sync_directory(destination.parent)
        except (OSError, ServiceError):
            destination.unlink(missing_ok=True)
            raise

    def prune(self, observation: tuple[float, set[str]]) -> None:
        now, protected = observation
        if not protected:
            raise ServiceError("backup_retention_protection_required", 503)
        for item in self.objects(self.options.prefix):
            name = item.key.removeprefix(self.options.prefix)
            if not ARCHIVE_NAME.fullmatch(name) or name.removesuffix(".encrypted") in protected:
                continue
            if now - item.modified <= self.options.retention_seconds:
                continue
            with self.client.open(self._request("DELETE", item.key)) as response:
                response.require_status(204)


def require_download_size(observed: int, expected: int) -> None:
    if observed != expected:
        raise ServiceError("incomplete_r2_archive", 503)


def r2_credentials(configuration: dict[str, Any], owner: int = 0) -> S3Credentials:
    record = protected_json(Path(configuration["backup_r2"]["credentials_path"]), owner)
    access, secret = record.get("access_key_id"), record.get("secret_access_key")
    if (
        not isinstance(access, str) or not re.fullmatch(r"[A-Za-z0-9]{16,128}", access)
        or not isinstance(secret, str) or not re.fullmatch(r"[A-Za-z0-9/+_=.-]{32,256}", secret)
    ):
        raise ServiceError("invalid_r2_credentials", 503)
    return S3Credentials(access, secret)


def r2_transfer(configuration: dict[str, Any], owner: int = 0) -> R2Transfer:
    options = R2Options.load(configuration)
    signer = SigV4(r2_credentials(configuration, owner), ("auto", "s3"))
    context = ssl.create_default_context()
    client = S3Http(signer, lambda timeout: http.client.HTTPSConnection(
        options.host, timeout=timeout, context=context,
    ))
    return R2Transfer(options, client)
