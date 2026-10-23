"""Durable matched cutover; no timer resumes before unlocked served-TLS verification."""

from pathlib import Path
from typing import Any, Protocol

from host.coordination import Coordination
from host.locks import HostLock
from host.release_files import RollbackPackage
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


class CutoverHost(Protocol):
    def schedule(self) -> dict[str, Any]: ...
    def pause(self) -> None: ...
    def snapshot(self) -> dict[str, Any]: ...
    def compatible(self, release: dict[str, Any]) -> None: ...
    def forward(self) -> None: ...
    def verify_service(self) -> None: ...
    def restore(self, release: dict[str, Any]) -> None: ...
    def verify_release(self, release: dict[str, Any]) -> None: ...
    def resume(self, schedule: dict[str, Any]) -> None: ...


class Cutover:
    def __init__(
        self,
        coordination: Coordination,
        dependencies: tuple[CutoverHost, RollbackPackage, Path],
    ) -> None:
        self.coordination = coordination
        self.host, self.package, self.outcome = dependencies

    def _record(self) -> dict[str, Any]:
        return protected_json(self.outcome, self.package.files.owner)

    def _phase(self, phase: str) -> None:
        record = self._record()
        record["phase"] = phase
        atomic_json(self.outcome, record)

    def run(self) -> None:
        with HostLock(self.outcome.with_suffix(".lock"), self.coordination.locks.policy):
            if self.outcome.exists():
                raise ServiceError("cutover_already_recorded", 409)
            atomic_json(self.outcome, {"phase": "pausing", "schedule": self.host.schedule()})
            try:
                self._forward()
            except (OSError, ServiceError):
                # Never repeat a possibly completed forward transition after interruption.
                self._phase("rollback_required")
                self.host.pause()
                raise

    def _forward(self) -> None:
        self.host.pause()
        with self.coordination.consistent():
            release = self.host.snapshot()
            self.host.compatible(release)
            record = self._record()
            record.update({"phase": "packaging", "release": release})
            atomic_json(self.outcome, record)
            identity = self.package.create(release)
            record.update({"phase": "installing", "package_sha256": identity})
            atomic_json(self.outcome, record)
            self.package.files.candidate()
            self._phase("activating")
            self.host.forward()
        self._phase("verifying_service")
        self.host.verify_service()
        self._phase("enabling_schedule")
        self.host.resume({"enabled": True, "active": True})
        self._phase("complete")

    def rollback(self) -> None:
        with HostLock(self.outcome.with_suffix(".lock"), self.coordination.locks.policy):
            record = self._record()
            if "release" not in record:
                raise ServiceError("cutover_snapshot_unavailable", 409)
            try:
                self._phase("rollback_pausing")
                self.host.pause()
                self._restore(record)
            except (OSError, ServiceError):
                self._phase("rollback_required")
                self.host.pause()
                raise

    def _restore(self, record: dict[str, Any]) -> None:
        with self.coordination.consistent():
            release = record["release"]
            self.host.compatible(release)
            if identity := record.get("package_sha256"):
                packaged = self.package.verify(identity)["release"]
                if packaged != release:
                    raise ServiceError("rollback_release_mismatch", 503)
                self._phase("rollback_installing")
                self.package.restore(identity)
                self._phase("rollback_activating")
                self.host.restore(release)
        self._phase("rollback_verifying")
        self.host.verify_release(release)
        self._phase("rollback_enabling_schedule")
        self.host.resume(record["schedule"])
        self._phase("rolled_back")
