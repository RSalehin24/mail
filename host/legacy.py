"""Pause and observe the non-cooperating legacy writer before baseline capture or hardening."""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from helper.process import Command, execute
from host.certificates import PairValidator, verify_served
from host.docker import Docker
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json


class LegacyRenewal:
    def __init__(self, record: Path, timeout: float, docker: Docker | None = None) -> None:
        self.record, self.timeout = record, timeout
        self.docker = docker or Docker(timeout=5)

    def _systemctl(self, arguments: tuple[str, ...]) -> str:
        result = execute(Command(("/usr/bin/systemctl",) + arguments, timeout=5))
        if result.code:
            raise ServiceError("legacy_service_observation_failed", 503)
        return result.output.strip()

    def _show(self, unit: str) -> dict[str, str]:
        output = self._systemctl(
            ("show", unit, "--property=ActiveState,UnitFileState,Result,ExecMainStatus")
        )
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)

    def enter(self, approval: dict[str, Any]) -> None:
        if approval.get("manual_renewal_inhibited") is not True:
            raise ServiceError("manual_renewal_exclusion_required", 403)
        if self.record.exists():
            raise ServiceError("maintenance_already_recorded", 409)
        atomic_json(
            self.record,
            {
                "timer": self._show("maddy-cert-renew.timer"),
                "service": self._show("maddy-cert-renew.service"),
                "state": "entering",
            },
        )
        self._systemctl(("disable", "--now", "maddy-cert-renew.timer"))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                self.verify()
                record = protected_json(self.record)
                record["state"] = "legacy_drained"
                atomic_json(self.record, record)
                return
            except ServiceError:
                time.sleep(0.25)
        raise ServiceError("legacy_completion_unknown", 503)

    def verify(self) -> None:
        self._verify_stopped()
        record = protected_json(self.record)
        if record.get("manual_completion") not in (None, "reconciled"):
            raise ServiceError("legacy_manual_completion_unknown", 409)

    def _verify_stopped(self) -> None:
        self.require_no_writers()
        timer = self._show("maddy-cert-renew.timer")
        service = self._show("maddy-cert-renew.service")
        if (
            timer.get("UnitFileState") not in {"disabled", "masked"}
            or timer.get("ActiveState") != "inactive"
        ):
            raise ServiceError("legacy_timer_not_paused", 409)
        if (
            service.get("ActiveState") not in {"inactive", "failed"}
            or service.get("Result") not in {"success", ""}
            or service.get("ExecMainStatus") != "0"
        ):
            raise ServiceError("legacy_completion_unknown", 409)

    def reconcile_manual(self) -> None:
        record = protected_json(self.record)
        if record.get("manual_completion") != "unknown":
            raise ServiceError("legacy_manual_reconciliation_not_required", 409)
        self._verify_stopped()
        certificate = PairValidator().validate(Path("/opt/maddy/tls"))
        verify_served(("127.0.0.1", 993), certificate["served_sha256"], self.timeout)
        record.update({
            "state": "legacy_drained", "manual_completion": "reconciled",
            "manual_reconciled_at": time.time(),
            "manual_reconciled_pair_sha256": certificate["served_sha256"],
            "manual_original_exit_code": None,
        })
        atomic_json(self.record, record)

    def require_no_writers(self) -> None:
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                self._check_process(entry)
        identifiers = self._container_identifiers()
        if not identifiers:
            return
        output = self.docker.run(("inspect", "--format", "{{json .Mounts}}") + identifiers)
        lines = output.splitlines()
        if len(lines) != len(identifiers):
            raise ServiceError("legacy_mount_observation_invalid", 503)
        for line in lines:
            self._require_read_only_mounts(line)

    def _container_identifiers(self) -> tuple[str, ...]:
        output = self.docker.run(("ps", "--no-trunc", "--format", "{{.ID}} {{.Names}}"))
        identifiers = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) != 2 or re.fullmatch(r"[0-9a-f]{64}", fields[0]) is None:
                raise ServiceError("legacy_container_observation_invalid", 503)
            if fields[1] != "maddy":
                identifiers.append(fields[0])
        if len(identifiers) > 64:
            raise ServiceError("legacy_container_observation_overloaded", 503)
        return tuple(identifiers)

    def _require_read_only_mounts(self, output: str) -> None:
        try:
            mounts = json.loads(output)
        except ValueError as error:
            raise ServiceError("legacy_mount_observation_invalid", 503) from error
        if not isinstance(mounts, list):
            raise ServiceError("legacy_mount_observation_invalid", 503)
        if any(self._writes_certificate_state(mount) for mount in mounts):
            raise ServiceError("legacy_container_writer_active", 409)

    @staticmethod
    def _writes_certificate_state(mount: Any) -> bool:
        if not isinstance(mount, dict) or type(mount.get("RW")) is not bool:
            raise ServiceError("legacy_mount_observation_invalid", 503)
        if not mount["RW"] or mount.get("Type") == "tmpfs":
            return False
        source = mount.get("Source")
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise ServiceError("legacy_mount_observation_invalid", 503)
        path = Path(source).resolve()
        directories = ("letsencrypt", "letsencrypt_lib", "tls")
        return any(
            path.is_relative_to(root) or root.is_relative_to(path)
            for name in directories
            for root in (Path("/opt/maddy", name).resolve(),)
        )

    def _check_process(self, entry: Path) -> None:
        if int(entry.name) == os.getpid():
            return
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except FileNotFoundError:
            return
        if any(b"/opt/maddy/renew-cert.sh" in argument for argument in arguments):
            if self._managed_legacy(entry):
                raise ServiceError("legacy_service_writer_active", 409)
            record = protected_json(self.record)
            record.update({"manual_completion": "unknown", "manual_writer_pid": int(entry.name)})
            atomic_json(self.record, record)
            raise ServiceError("legacy_manual_run_active", 409)

    @staticmethod
    def _managed_legacy(entry: Path) -> bool:
        try:
            with (entry / "cgroup").open("rb") as source:
                value = source.read(65537)
        except FileNotFoundError:
            return False
        if len(value) > 65536:
            raise ServiceError("legacy_process_observation_invalid", 503)
        group = b"/system.slice/maddy-cert-renew.service"
        paths = [line.rsplit(b":", 1)[-1] for line in value.splitlines()]
        return any(path == group or path.startswith(group + b"/") for path in paths)
