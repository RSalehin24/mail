"""Reuse the tested image for encryption and recovery, with no host auth dependency install."""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from host.container_identity import ContainerIdentity
from host.container_policy import TEMPORARY_PATH, ContainerPolicy
from host.docker import Docker
from host.preflight import validate_image
from shared.errors import ServiceError


def require_private_terminal() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ServiceError("private_terminal_required", 403)


class ImageTools:
    def __init__(self, docker: Docker, deployment: dict[str, Any]) -> None:
        self.docker, self.deployment = docker, deployment

    def base(self) -> tuple[str, ...]:
        return (
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "0:0",
        ) + ContainerPolicy.load(self.deployment).arguments((TEMPORARY_PATH,))

    def authentication_arguments(
        self, action: str, destination: Path | None = None,
    ) -> tuple[str, ...]:
        sources = self._restored_sources(destination) if destination is not None else (
            (Path("/opt/mail-admin/data"), "/app/data"),
            (Path("/etc/mail-admin/secrets"), "/run/secrets/mail-admin"),
            (Path("/etc/mail-admin/installation.json"), "/run/mail-admin-installation.json"),
        )
        return self._authentication_arguments(action, sources)

    def _restored_sources(self, destination: Path) -> tuple[tuple[Path, str], ...]:
        image = self.deployment.get("dashboard_image_id")
        if not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ServiceError("immutable_recovery_image_required", 503)
        validate_image(self.deployment, self.docker)
        sources = (
            (destination / "dashboard", "/app/data"),
            (destination / "operator/secrets", "/run/secrets/mail-admin"),
            (destination / "operator/installation.json", "/run/mail-admin-installation.json"),
        )
        for source, _ in sources:
            if source.is_symlink() or not source.exists() or any(
                character in str(source) for character in ",\r\n\0"
            ):
                raise ServiceError("unsafe_recovery_mount", 503)
        return sources

    def prepare_restored_authentication(self, destination: Path, name: str) -> None:
        arguments = self.authentication_arguments("restore-prepare", destination)
        identity = ContainerIdentity(self.docker, ("recovery", name))
        if identity.find():
            raise ServiceError("recovery_container_already_exists", 409)
        self.docker.run(("create", "--name", name, "--label", identity.label + "=" + name)
                        + arguments[2:])
        self.docker.run(("start", "--attach", name))
        self.verify_utility_exit(name)

    def _authentication_arguments(
        self, action: str, sources: tuple[tuple[Path, str], ...],
    ) -> tuple[str, ...]:
        base = self._private_base() if action in {"recover", "bootstrap"} else self.base()
        arguments = base + (
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
        )
        for source, target in sources:
            readonly = "" if target == "/app/data" else ",readonly"
            arguments += ("--mount", f"type=bind,src={source},dst={target}{readonly}")
        for key, value in self.deployment["consumers"]["mail-admin"]["environment"].items():
            arguments += ("-e", f"{key}={value}")
        return arguments + (
            self.deployment["dashboard_image_id"],
            "python",
            "-m",
            "backend.operator",
            action,
            "--uid",
            str(self.deployment["inputs"]["service_uid"]),
            "--gid",
            str(self.deployment["inputs"]["service_gid"]),
        )

    def _private_base(self) -> tuple[str, ...]:
        arguments = iter(self.base())
        private = []
        for argument in arguments:
            if argument in {"--log-driver", "--log-opt"}:
                next(arguments)
            else:
                private.append(argument)
        return tuple(private) + ("--log-driver", "none")

    def provision(self) -> None:
        arguments = self.base() + (
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "CHOWN",
            "--mount",
            "type=bind,src=/opt/mail-admin/data,dst=/app/data",
            "--mount",
            "type=bind,src=/etc/mail-admin,dst=/run/operator",
            self.deployment["dashboard_image_id"],
            "python",
            "-m",
            "backend.operator",
            "provision",
            "--uid",
            str(self.deployment["inputs"]["service_uid"]),
            "--gid",
            str(self.deployment["inputs"]["service_gid"]),
        )
        self.docker.run(arguments)

    def encrypt(self, source: Path, destination: Path) -> None:
        workspace = destination.with_name(destination.stem + "-cipher")
        workspace.mkdir(mode=0o700)
        shutil.copy2(source, workspace / "plaintext.tar")
        arguments = self.base() + (
            "--mount",
            f"type=bind,src={workspace},dst=/archive",
            "--mount",
            "type=bind,src=" + self.deployment["backup_key_path"] + ",dst=/archive-key,readonly",
            self.deployment["dashboard_image_id"],
            "python",
            "-m",
            "backend.operator",
            "encrypt",
        )
        self.docker.run(arguments)
        (workspace / "sealed.bin").rename(destination)

    def extract(self, source: Path, destination: Path) -> None:
        if destination.exists():
            raise ServiceError("restore_target_exists", 409)
        workspace = destination.with_name(destination.name + "-cipher")
        workspace.mkdir(mode=0o700)
        shutil.copy2(source, workspace / "sealed.bin")
        arguments = self.base() + (
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--mount",
            f"type=bind,src={workspace},dst=/archive",
            "--mount",
            "type=bind,src=" + self.deployment["backup_key_path"] + ",dst=/archive-key,readonly",
        )
        for action in ("decrypt", "extract"):
            self.docker.run(
                arguments
                + (
                    self.deployment["dashboard_image_id"],
                    "python",
                    "-m",
                    "backend.operator",
                    action,
                )
            )
        (workspace / "restored").rename(destination)

    def offline_utility(self, name: str, destination: Path | None = None) -> None:
        require_private_terminal()
        identity = ContainerIdentity(self.docker, ("recovery", name))
        if identity.find():
            raise ServiceError("recovery_container_already_exists", 409)
        base = self.authentication_arguments("recover", destination)
        arguments = (
            "create",
            "-it",
            "--name",
            name,
            "--label",
            identity.label + "=" + name,
        ) + base[2:]
        self.docker.run(arguments)
        attached = (self.docker.executable, "start", "--attach", "--interactive", name)
        # Inherit only the operator's terminal; never capture credentials or recovery material.
        result = subprocess.run(attached, check=False)  # noqa: S603 -- fixed isolated image utility.
        if result.returncode:
            raise ServiceError("offline_recovery_incomplete", 503)
        self.verify_recovery(name, destination)

    def verify_recovery(self, name: str, destination: Path | None = None) -> None:
        self.verify_utility_exit(name)
        self.docker.run(self.authentication_arguments("probe", destination))

    def verify_utility_exit(self, name: str) -> None:
        identity = ContainerIdentity(self.docker, ("recovery", name))
        identifier = identity.find()
        if not identifier:
            raise ServiceError("recovery_completion_unknown", 409)
        observation = identity.observe(identifier)
        if (
            observation["State"]["Running"]
            or observation["State"]["Status"] != "exited"
            or observation["State"]["ExitCode"] != 0
            or observation["Image"] != self.deployment["dashboard_image_id"]
        ):
            raise ServiceError("recovery_completion_unknown", 409)

    def discard_recovery(self, name: str) -> None:
        identity = ContainerIdentity(self.docker, ("recovery", name))
        identifier = identity.find()
        if identifier:
            identity.remove_stopped(identifier)
