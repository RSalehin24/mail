"""Durable immutable TLS generations and independent per-consumer activation."""

import hashlib
import os
import re
import shutil
import socket
import ssl
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from helper.process import Command, execute
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json, sync_directory
from shared.installation import site


@dataclass(frozen=True)
class CertificatePolicy:
    group: int
    owner: int = 0
    trust_file: Path | None = None


class PairValidator:
    def __init__(
        self, trust_file: Path | None = None, executable: str = "/usr/bin/openssl"
    ) -> None:
        self.trust_file, self.executable = trust_file, executable

    def _openssl(self, arguments: tuple[str, ...]) -> str:
        result = execute(Command((self.executable,) + arguments))
        if result.code:
            raise ServiceError("certificate_validation_failed", 503)
        return result.output

    def validate(self, directory: Path) -> dict[str, Any]:
        chain, key = str(directory / "fullchain.pem"), str(directory / "privkey.pem")
        public = self._openssl(("x509", "-in", chain, "-pubkey", "-noout"))
        private_public = self._openssl(("pkey", "-in", key, "-pubout"))
        if public != private_public:
            raise ServiceError("certificate_key_mismatch", 503)
        trust = ("-CAfile", str(self.trust_file)) if self.trust_file else ()
        self._openssl(
            ("verify",)
            + trust
            + (
                "-purpose",
                "sslserver",
                "-verify_hostname",
                site().mail_hostname,
                "-untrusted",
                chain,
                chain,
            )
        )
        expiry = self._openssl(("x509", "-in", chain, "-enddate", "-noout")).strip()
        timestamp = datetime.strptime(expiry, "notAfter=%b %d %H:%M:%S %Y GMT").replace(tzinfo=UTC)
        leaf = Path(chain).read_text().split("-----END CERTIFICATE-----", 1)[0]
        der = ssl.PEM_cert_to_DER_cert(leaf + "-----END CERTIFICATE-----\n")
        return {"served_sha256": hashlib.sha256(der).hexdigest(), "expiry": timestamp.timestamp()}


class Consumer(Protocol):
    def activate(self, generation: Path, certificate: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class GenerationPaths:
    directory: Path
    state: Path


class Generations:
    def __init__(
        self,
        paths: GenerationPaths,
        dependencies: tuple[CertificatePolicy, PairValidator, Callable[[], float]],
    ) -> None:
        self.paths = paths
        self.policy, self.validator, self.clock = dependencies

    def read(self) -> dict[str, Any]:
        return protected_json(self.paths.state, self.policy.owner)

    def write(self, record: dict[str, Any]) -> None:
        record["observed_at"] = self.clock()
        atomic_json(self.paths.state, record)

    def stage(self, source: Path) -> Path:
        self.validator.validate(source)
        pair = [(source / name).read_bytes() for name in ("fullchain.pem", "privkey.pem")]
        identifier = hashlib.sha256(pair[0] + b"\0" + pair[1]).hexdigest()
        published = self.paths.directory / identifier
        if published.exists():
            self.validator.validate(published)
            return published
        staging = self.paths.directory / (".staging-" + str(uuid.uuid4()))
        staging.mkdir(mode=0o750)
        os.chown(staging, self.policy.owner, self.policy.group)
        directory_mode = stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
        os.chmod(staging, directory_mode)
        for name, value in zip(("fullchain.pem", "privkey.pem"), pair, strict=True):
            self._stage_file(staging / name, value)
        sync_directory(staging)
        self.validator.validate(staging)
        os.rename(staging, published)
        sync_directory(self.paths.directory)
        return published

    def _stage_file(self, path: Path, value: bytes) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        os.fchown(descriptor, self.policy.owner, self.policy.group)
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())

    def publish(self, generation: Path) -> None:
        certificate = self.validator.validate(generation)
        state = self.read()
        state.update({"desired_generation": generation.name, **certificate, "outcome": "pending"})
        self.write(state)

    def activate(self, consumers: dict[str, Consumer]) -> None:
        state = self.read()
        if not re.fullmatch(r"[0-9a-f]{64}", state.get("desired_generation", "")):
            raise ServiceError("invalid_desired_generation", 503)
        generation = self.paths.directory / state["desired_generation"]
        certificate = self.validator.validate(generation)
        failures = []
        for name, record in state["consumers"].items():
            if record["intent"] in {"never_deployed", "maintenance"}:
                continue
            try:
                consumers[name].activate(generation, certificate)
                record.update(
                    {
                        "active_generation": generation.name,
                        "state": "active",
                        "served_sha256": certificate["served_sha256"],
                        "served_expiry": certificate["expiry"],
                    }
                )
            except (OSError, ServiceError):
                record["state"] = "pending"
                failures.append(name)
            self.write(state)
        state["outcome"] = "activation_pending" if failures else "active"
        self.write(state)
        if failures:
            raise ServiceError("certificate_activation_pending", 503)

    def register(self, consumer: str) -> None:
        if consumer not in {"maddy", "mail-admin"}:
            raise ServiceError("unknown_consumer", 400)
        state = self.read()
        if consumer == "mail-admin" and state.get("coordination_migrated") is not True:
            raise ServiceError("matched_renewal_cutover_required", 409)
        previous = state["consumers"].get(consumer, {})
        state["consumers"][consumer] = {
            "intent": "expected_running",
            "state": "pending",
            "active_generation": previous.get("active_generation"),
            **{key: previous[key] for key in ("served_sha256", "served_expiry") if key in previous},
        }
        self.write(state)

    def maintenance(self, consumer: str, reason: str) -> None:
        state = self.read()
        state["consumers"][consumer].update(
            {"intent": "maintenance", "reason": reason, "state": "operator_paused"}
        )
        self.write(state)

    def referenced_generations(self) -> set[str]:
        state = self.read()
        references = {state["desired_generation"], *state.get("rollback_generations", [])}
        references.update(
            record["active_generation"]
            for record in state["consumers"].values()
            if record.get("active_generation")
        )
        return references

    def prune(self, retention_seconds: float) -> None:
        if retention_seconds <= 0:
            raise ServiceError("generation_retention_required", 503)
        references = self.referenced_generations()
        for path in self.paths.directory.iterdir():
            metadata = path.lstat()
            eligible = re.fullmatch(r"[0-9a-f]{64}|\.staging-[0-9a-f-]{36}", path.name)
            if (
                eligible
                and path.name not in references
                and self.clock() - metadata.st_mtime > retention_seconds
            ):
                if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != self.policy.owner:
                    raise ServiceError("unsafe_generation_directory", 503)
                shutil.rmtree(path)
        sync_directory(self.paths.directory)


def served_certificate(endpoint: tuple[str, int], trust: Path | None = None) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(trust) if trust else None)
    with socket.create_connection(endpoint, timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=site().mail_hostname) as secured:
            certificate = secured.getpeercert(binary_form=True)
            if certificate is None:
                raise ServiceError("served_certificate_missing", 503)
            parsed = secured.getpeercert()
            expiry = parsed.get("notAfter") if parsed else None
            if not isinstance(expiry, str):
                raise ServiceError("served_certificate_expiry_missing", 503)
            return {
                "served_sha256": hashlib.sha256(certificate).hexdigest(),
                "expiry": ssl.cert_time_to_seconds(expiry),
            }


def served_fingerprint(endpoint: tuple[str, int], trust: Path | None = None) -> str:
    return str(served_certificate(endpoint, trust)["served_sha256"])


def verify_served(endpoint: tuple[str, int], expected: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if served_fingerprint(endpoint) == expected:
                return
        except (OSError, ServiceError) as error:
            last_error = error
        time.sleep(0.1)
    raise ServiceError("served_generation_mismatch", 503) from last_error
