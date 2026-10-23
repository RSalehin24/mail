"""Certbot state survives host-process death; no released lock implies writer completion."""

import uuid
from pathlib import Path
from typing import Any

from host.certificates import Consumer, Generations
from host.container_identity import ContainerIdentity
from host.container_policy import TEMPORARY_PATH, ContainerPolicy
from host.coordination import Coordination
from host.docker import Docker
from host.locks import HostLock
from shared.errors import ServiceError


class Certbot:
    def __init__(self, docker: Docker, dependencies: tuple[Generations, dict[str, Any]]) -> None:
        self.docker = docker
        self.generations, self.configuration = dependencies

    def acquire(self, mode: str = "renew") -> None:
        if mode not in {"renew", "dry-run"}:
            raise ServiceError("invalid_renewal_mode", 400)
        name = "mail-admin-certbot-" + str(uuid.uuid4())
        arguments = self._arguments(name, mode)
        self.reconcile()
        self._cleanup_completed()
        state = self.generations.read()
        state["acme_run"] = {"name": name, "state": "creating", "container_id": None}
        self.generations.write(state)
        identifier = self.docker.run(arguments)
        state["acme_run"].update({"container_id": identifier, "state": "starting"})
        self.generations.write(state)
        try:
            self.docker.run(("start", "--attach", identifier))
        except (OSError, ServiceError) as error:
            state["acme_run"]["state"] = "unknown"
            self.generations.write(state)
            raise ServiceError("acme_completion_unknown", 503) from error
        self.reconcile()
        if self.generations.read()["acme_run"].get("exit_code") != 0:
            raise ServiceError("acme_renewal_failed", 503)

    def _arguments(self, name: str, mode: str) -> tuple[str, ...]:
        policy = ContainerPolicy.load(self.configuration)
        limits = policy.arguments((TEMPORARY_PATH, "/var/log/letsencrypt"))
        mounts = (
            "--mount",
            "type=bind,src=/opt/maddy/letsencrypt,dst=/etc/letsencrypt",
            "--mount",
            "type=bind,src=/opt/maddy/letsencrypt_lib,dst=/var/lib/letsencrypt",
        )
        arguments: tuple[str, ...] = (
            (
                "create",
                "--name",
                name,
                "--restart",
                "no",
                "--pull",
                "never",
                "--label",
                "mail-admin.certbot-run=" + name,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--cap-add",
                "NET_BIND_SERVICE",
                "--security-opt",
                "no-new-privileges:true",
                "-p",
                "80:80",
            )
            + limits
            + mounts
        )
        arguments += (self.configuration["certbot_image"], "renew", "--non-interactive")
        if mode == "dry-run":
            arguments += ("--dry-run",)
        return arguments

    def reconcile(self) -> None:
        state = self.generations.read()
        previous = state.get("acme_run")
        if not previous or previous["state"] in {"completed", "not_started"}:
            return
        if previous["state"] == "creating" and not previous.get("container_id"):
            identifier = self._find(previous)
            if not identifier:
                previous["state"] = "not_started"
                self.generations.write(state)
                return
            previous["container_id"] = identifier
            self.generations.write(state)
        if not previous.get("container_id"):
            raise ServiceError("acme_completion_unknown", 409)
        observation = self._observation(previous)
        if observation["State"]["Status"] == "created":
            self.docker.run(("rm", previous["container_id"]))
            previous.update({"state": "not_started", "container_removed": True})
            self.generations.write(state)
            return
        if observation["State"]["Running"] or observation["State"]["Status"] != "exited":
            raise ServiceError("acme_completion_unknown", 409)
        previous.update({"state": "completed", "exit_code": observation["State"]["ExitCode"]})
        self.generations.write(state)

    def _find(self, run: dict[str, Any]) -> str:
        return ContainerIdentity(self.docker, ("certbot", run.get("name", ""))).find()

    def _observation(self, run: dict[str, Any]) -> dict[str, Any]:
        return ContainerIdentity(self.docker, ("certbot", run.get("name", ""))).observe(
            run["container_id"]
        )

    def _cleanup_completed(self) -> None:
        state = self.generations.read()
        previous = state.get("acme_run")
        if not previous or previous["state"] != "completed" or previous.get("container_removed"):
            return
        identifier = self._find(previous)
        if identifier:
            if identifier != previous["container_id"]:
                raise ServiceError("untrusted_acme_container", 503)
            observation = self._observation(previous)
            if observation["State"]["Status"] != "exited":
                raise ServiceError("acme_completion_unknown", 409)
            self.docker.run(("rm", identifier))
        previous["container_removed"] = True
        self.generations.write(state)


class Renewal:
    def __init__(
        self, coordination: Coordination, dependencies: tuple[Certbot, dict[str, Consumer]]
    ) -> None:
        self.coordination = coordination
        self.certbot, self.consumers = dependencies

    def run(self, source: Path, mode: str = "renew") -> None:
        locks = self.coordination.locks
        with HostLock(locks.renewal, locks.policy):
            self.certbot.acquire(mode)
            if mode == "dry-run":
                return
            with HostLock(locks.maddy, locks.policy):
                self.coordination.require_quiet()
                generations = self.coordination.generations
                generations.publish(generations.stage(source))
                generations.activate(self.consumers)

    def retry_activation(self) -> None:
        with self.coordination.consistent():
            self.coordination.generations.activate(self.consumers)
