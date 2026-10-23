"""Identify a durable one-off container without trusting a reused Docker name."""

import re
from typing import Any

from host.docker import Docker
from shared.errors import ServiceError


class ContainerIdentity:
    def __init__(self, docker: Docker, reference: tuple[str, str]) -> None:
        self.docker = docker
        self.kind, self.name = reference
        if self.kind not in {"certbot", "recovery"} or not isinstance(self.name, str):
            raise ServiceError("untrusted_operation_container", 503)
        if not re.fullmatch("mail-admin-" + self.kind + r"-[0-9a-f-]{36}", self.name):
            raise ServiceError("untrusted_operation_container", 503)

    @property
    def label(self) -> str:
        return "mail-admin." + self.kind + "-run"

    def find(self) -> str:
        identifiers = self.docker.run(
            (
                "container",
                "ls",
                "--all",
                "--no-trunc",
                "--filter",
                "name=^/" + self.name + "$",
                "--format",
                "{{.ID}}",
            )
        ).splitlines()
        if len(identifiers) > 1:
            raise ServiceError("operation_container_identity_unknown", 503)
        return identifiers[0] if identifiers else ""

    def observe(self, identifier: str) -> dict[str, Any]:
        observed = self.docker.inspect(identifier)
        if (
            observed.get("Id") != identifier
            or observed.get("Name") != "/" + self.name
            or observed.get("Config", {}).get("Labels", {}).get(self.label) != self.name
        ):
            raise ServiceError("untrusted_operation_container", 503)
        return observed

    def remove_stopped(self, identifier: str) -> None:
        observation = self.observe(identifier)
        if observation["State"]["Running"] or observation["State"]["Status"] not in {
            "created",
            "exited",
        }:
            raise ServiceError("operation_container_still_active", 409)
        self.docker.run(("rm", identifier))
