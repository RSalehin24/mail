"""Root adapters for the approved, same-schema TLS/renewal switch and its rollback."""

import copy
import sqlite3
import time
from pathlib import Path
from typing import Any

from helper.maddy import LAUNCHER
from helper.process import Command, execute
from host.certificates import verify_served
from host.context import HostContext
from host.docker import DockerConsumer
from host.legacy import LegacyRenewal
from host.release_files import ReleaseFiles, file_digest
from host.rollout import rollout_directory
from shared.errors import ServiceError
from shared.files import protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from shared.validation import HELPER_PROTOCOL, SCHEMA_VERSION


class Systemd:
    def __init__(self, timeout: float) -> None:
        self.timeout = timeout

    def run(self, arguments: tuple[str, ...]) -> str:
        response = execute(Command(("/usr/bin/systemctl",) + arguments, timeout=self.timeout))
        if response.code:
            raise ServiceError("cutover_systemd_failed", 503)
        return response.output.strip()

    def show(self, unit: str, properties: str) -> dict[str, str]:
        output = self.run(("show", unit, "--property=" + properties))
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


class RootCutoverHost:
    def __init__(self, context: HostContext, files: ReleaseFiles) -> None:
        self.context, self.files = context, files
        self.configuration = context.deployment["cutover"]
        self.systemd = Systemd(context.deployment["renewal_timeout_seconds"])
        self.legacy = LegacyRenewal(rollout_directory(context.deployment) / "legacy.json", 30)
        self._validate_plan()

    def _validate_plan(self) -> None:
        expected = {
            "renewal_service": "/etc/systemd/system/maddy-cert-renew.service",
            "renewal_timer": "/etc/systemd/system/maddy-cert-renew.timer",
            "renewal_script": "/opt/maddy/renew-cert.sh",
            "maddy_compose": self.context.deployment["consumers"]["maddy"]["compose_file"],
        }
        if {name: record["target"] for name, record in self.files.plan.items()} != expected:
            raise ServiceError("cutover_file_roles_mismatch", 503)
        root = Path("/usr/local/lib/mail-admin")
        required = {
            str(path)
            for directory in ("host", "helper", "shared")
            for path in (root / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        }
        required.update(
            {
                str(root / "helper/service.py"),
                str(root / "host/cli.py"),
                LAUNCHER,
                "/etc/systemd/system/mail-admin-helper.service",
            }
        )
        sources = self.configuration.get("support_files")
        if (
            not isinstance(sources, list)
            or not all(isinstance(path, str) for path in sources)
            or not required <= set(sources)
        ):
            raise ServiceError("rollback_support_files_incomplete", 503)

    def schedule(self) -> dict[str, Any]:
        self.legacy.verify()
        timer = protected_json(self.legacy.record)["timer"]
        if timer.get("UnitFileState") not in {"enabled", "disabled"} or timer.get(
            "ActiveState"
        ) not in {"active", "inactive"}:
            raise ServiceError("unsupported_original_timer_state", 409)
        return {
            "enabled": timer["UnitFileState"] == "enabled",
            "active": timer["ActiveState"] == "active",
        }

    def pause(self) -> None:
        self.systemd.run(("disable", "--now", "maddy-cert-renew.timer"))
        deadline = time.monotonic() + self.context.deployment["inputs"]["operation_timeout_seconds"]
        while time.monotonic() < deadline:
            if self._quiet_service():
                return
            time.sleep(0.25)
        raise ServiceError("cutover_renewal_not_quiet", 409)

    def _quiet_service(self) -> bool:
        observation = self.systemd.show(
            "maddy-cert-renew.service", "ActiveState,MainPID,ControlPID,Result,ExecMainStatus"
        )
        if (
            observation.get("ActiveState") not in {"inactive", "failed"}
            or observation.get("MainPID") != "0"
            or observation.get("ControlPID") != "0"
        ):
            return False
        self.legacy.require_no_writers()
        service = self.files.plan["renewal_service"]
        if file_digest(Path(service["target"])) == service["sha256"]:
            self.context.renewal().certbot.reconcile()
            self.context.coordination.require_quiet()
        else:
            self.legacy.verify()
        return True

    def snapshot(self) -> dict[str, Any]:
        state = self.context.generations.read()
        if any(record["intent"] == "maintenance" for record in state["consumers"].values()):
            raise ServiceError("consumer_maintenance_active", 409)
        consumers = {
            name: self._consumer(name)
            for name, record in state["consumers"].items()
            if record["intent"] == "expected_running"
        }
        return {
            "artifact_set_sha256": self.context.deployment["artifact_set_sha256"],
            "schema_version": self.context.deployment["schema_version"],
            "helper_protocol": self.context.deployment["helper_protocol"],
            "tls_layout": "generations" if state.get("coordination_migrated") else "legacy",
            "certificate_state": copy.deepcopy(state),
            "consumers": consumers,
            "support_files": {
                path: self._support_digest(Path(path))
                for path in self.configuration["support_files"]
            },
        }

    def _support_digest(self, path: Path) -> str:
        self.files.read(path)
        return file_digest(path)

    def _consumer(self, name: str) -> dict[str, Any]:
        record = copy.deepcopy(self.context.deployment["consumers"][name])
        container = self.context.docker.inspect(name)
        mounts = {mount["Destination"]: mount["Source"] for mount in container["Mounts"]}
        if container["Image"] != record["image_id"] or not container["State"]["Running"]:
            raise ServiceError("cutover_consumer_identity_mismatch", 409)
        directory = Path(mounts[record["tls_target"]])
        certificate = self.context.generations.validator.validate(directory)
        verify_served(("127.0.0.1", record["tls_port"]), certificate["served_sha256"], 5)
        return {
            **record,
            "tls_directory": str(directory),
            "certificate": certificate,
            "mounts": mounts,
            "entrypoint": container["Config"]["Entrypoint"],
            "command": container["Config"]["Cmd"],
        }

    def compatible(self, release: dict[str, Any]) -> None:
        state = self.context.generations.read()
        if any(
            record.get("reason") == "offline_recovery"
            for record in state["consumers"].values()
        ):
            raise ServiceError("offline_recovery_in_progress", 409)
        if (
            release["schema_version"] != SCHEMA_VERSION
            or release["helper_protocol"] != HELPER_PROTOCOL
            or release["artifact_set_sha256"] != self.context.deployment["artifact_set_sha256"]
        ):
            raise ServiceError("incompatible_rollback_set", 409)
        for path, identity in release["support_files"].items():
            if self._support_digest(Path(path)) != identity:
                raise ServiceError("rollback_support_file_drift", 409)
        database = Path("/opt/mail-admin/data/admin_data.db")
        if "mail-admin" in release["consumers"] and not database.exists():
            raise ServiceError("rollback_dashboard_database_missing", 409)
        if database.exists():
            self._schema(database, release["schema_version"])

    @staticmethod
    def _schema(path: Path, expected: int) -> None:
        if path.is_symlink() or not path.is_file():
            raise ServiceError("invalid_rollback_database", 409)
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25) as connection:
                if connection.execute("PRAGMA user_version").fetchone()[0] != expected:
                    raise ServiceError("incompatible_rollback_schema", 409)
        except sqlite3.Error as error:
            raise ServiceError("rollback_schema_unavailable", 503) from error

    def forward(self) -> None:
        self.systemd.run(("daemon-reload",))
        self._loaded_service()
        state = self.context.generations.read()
        state["coordination_migrated"] = True
        self.context.generations.write(state)
        self.context.generations.activate(
            {
                name: DockerConsumer(record, self.context.docker)
                for name, record in self.context.deployment["consumers"].items()
            }
        )

    def _loaded_service(self) -> None:
        expected = self.files.plan["renewal_service"]
        observation = self.systemd.show(
            "maddy-cert-renew.service", "FragmentPath,DropInPaths,ExecStart"
        )
        if (
            observation.get("FragmentPath") != expected["target"]
            or observation.get("DropInPaths") != ""
            or "argv[]=/usr/bin/python3 -m host.cli renew ;" not in observation.get("ExecStart", "")
            or file_digest(Path(expected["target"])) != expected["sha256"]
        ):
            raise ServiceError("loaded_renewal_service_mismatch", 409)
        self._loaded_timer()

    def _loaded_timer(self) -> None:
        expected = self.files.plan["renewal_timer"]
        observation = self.systemd.show(
            "maddy-cert-renew.timer", "FragmentPath,DropInPaths,TimersCalendar"
        )
        if (
            observation.get("FragmentPath") != expected["target"]
            or observation.get("DropInPaths") != ""
            or "OnCalendar=*-*-* 03:00:00 UTC" not in observation.get("TimersCalendar", "")
            or file_digest(Path(expected["target"])) != expected["sha256"]
        ):
            raise ServiceError("loaded_renewal_timer_mismatch", 409)

    def verify_service(self) -> None:
        self._loaded_service()
        self.systemd.run(("start", "maddy-cert-renew.service"))
        self.context.renewal().retry_activation()
        state = self.context.generations.read()
        directory = self.context.generations.paths.directory / state["desired_generation"]
        for name, consumer in state["consumers"].items():
            if consumer["intent"] == "expected_running":
                actual = self._consumer(name)
                if (
                    consumer.get("active_generation") != state["desired_generation"]
                    or actual["tls_directory"] != str(directory)
                ):
                    raise ServiceError("cutover_activation_unverified", 503)

    def restore(self, release: dict[str, Any]) -> None:
        self.systemd.run(("daemon-reload",))
        current = self.context.generations.read()
        state = copy.deepcopy(release["certificate_state"])
        dashboard = current["consumers"].get("mail-admin", {})
        if "mail-admin" not in release["consumers"] and dashboard.get("intent") != "never_deployed":
            self._pause_new_dashboard()
            state["consumers"]["mail-admin"] = {
                **dashboard,
                "intent": "maintenance",
                "reason": "rollback_to_maddy_only",
                "state": "operator_paused",
            }
        references = self.context.generations.referenced_generations()
        state["rollback_generations"] = sorted(
            references | set(state.get("rollback_generations", []))
        )
        # Completion observation is current even when the selected certificate rolls back.
        if "acme_run" in current:
            state["acme_run"] = current["acme_run"]
        self.context.generations.write(state)
        for record in release["consumers"].values():
            directory = Path(record["tls_directory"])
            certificate = self.context.generations.validator.validate(directory)
            DockerConsumer(record, self.context.docker).activate(directory, certificate)

    def _pause_new_dashboard(self) -> None:
        RuntimePolicy(
            Path("/etc/mail-admin/installation.json"),
            Path("/etc/mail-admin/secrets") / RUNTIME_POLICY_NAME,
        ).pause()
        self.context.docker.stop_for_maintenance("mail-admin")

    def verify_release(self, release: dict[str, Any]) -> None:
        self.compatible(release)
        state = self.context.generations.read()
        dashboard = state["consumers"].get("mail-admin", {})
        if "mail-admin" not in release["consumers"] and dashboard.get("intent") != "never_deployed":
            container = self.context.docker.inspect("mail-admin")
            if (
                dashboard.get("reason") != "rollback_to_maddy_only"
                or dashboard.get("intent") != "maintenance"
                or container["State"]["Running"]
                or container["HostConfig"]["RestartPolicy"]["Name"] != "no"
            ):
                raise ServiceError("rollback_dashboard_not_paused", 503)
        for name, record in release["consumers"].items():
            actual = self._consumer(name)
            if any(
                actual[key] != record[key]
                for key in ("image_id", "mounts", "entrypoint", "command", "certificate")
            ):
                raise ServiceError("rollback_consumer_binding_mismatch", 503)

    def resume(self, schedule: dict[str, Any]) -> None:
        if type(schedule.get("enabled")) is not bool or type(schedule.get("active")) is not bool:
            raise ServiceError("invalid_cutover_schedule", 503)
        unit = "maddy-cert-renew.timer"
        self.systemd.run(("enable" if schedule["enabled"] else "disable", unit))
        self.systemd.run(("start" if schedule["active"] else "stop", unit))
        observation = self.systemd.show(unit, "UnitFileState,ActiveState")
        if observation != {
            "UnitFileState": "enabled" if schedule["enabled"] else "disabled",
            "ActiveState": "active" if schedule["active"] else "inactive",
        }:
            raise ServiceError("cutover_timer_state_mismatch", 503)
