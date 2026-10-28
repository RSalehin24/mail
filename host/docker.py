"""Root-only Docker lifecycle adapters. No web/helper operation exposes recreation."""

import json
import logging
import time
from pathlib import Path
from typing import Any

from helper.maddy import DockerMaddy
from helper.process import Command, execute
from host.certificates import Generations, served_fingerprint, verify_served
from shared.errors import ServiceError


class Docker:
    def __init__(self, executable: str = "/usr/bin/docker", timeout: float = 30) -> None:
        self.executable, self.timeout = executable, timeout

    def run(self, arguments: tuple[str, ...], environment: dict[str, str] | None = None) -> str:
        # A graceful host stop may use its declared 30 seconds before Docker kills it.
        # Keep the configured command overhead and every mailbox/other command bound unchanged.
        timeout = self.timeout + 30 if arguments[:3] == ("stop", "--time", "30") else self.timeout
        response = execute(
            Command((self.executable,) + arguments, timeout=timeout, environment=environment)
        )
        if response.code:
            raise ServiceError("docker_operation_failed", 503)
        return response.output.strip()

    def inspect(self, name: str) -> dict[str, Any]:
        result = json.loads(self.run(("inspect", name)))
        if len(result) != 1:
            raise ServiceError("docker_identity_unknown", 503)
        return dict(result[0])

    def stop_for_maintenance(self, name: str) -> None:
        if name not in {"maddy", "mail-admin"}:
            raise ServiceError("unknown_consumer", 400)
        self.run(("update", "--restart", "no", name))
        if self.inspect(name)["HostConfig"]["RestartPolicy"]["Name"] != "no":
            raise ServiceError("consumer_restart_pause_incomplete", 503)
        self.run(("stop", "--time", "30", name))
        if self.inspect(name)["State"]["Running"]:
            raise ServiceError("consumer_stop_incomplete", 503)


class DockerConsumer:
    def __init__(self, record: dict[str, Any], docker: Docker) -> None:
        self.record, self.docker = record, docker

    def activate(self, generation: Path, certificate: dict[str, Any]) -> None:
        container = self.docker.inspect(self.record["container"])
        if self._matches(container, generation) and container["State"]["Running"]:
            if self._served(certificate):
                return
        environment = {**self.record["environment"], self.record["tls_variable"]: str(generation)}
        arguments = (
            "compose",
            "-f",
            self.record["compose_file"],
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
            "--no-deps",
            "--force-recreate",
            self.record["service"],
        )
        self.docker.run(arguments, environment)
        container = self.docker.inspect(self.record["container"])
        if not self._matches(container, generation) or not container["State"]["Running"]:
            raise ServiceError("consumer_binding_mismatch", 503)
        deadline = time.monotonic() + self.docker.timeout
        while time.monotonic() < deadline:
            if self._served(certificate):
                return
            time.sleep(0.1)
        raise ServiceError("served_generation_mismatch", 503)

    def _matches(self, container: dict[str, Any], generation: Path) -> bool:
        actual = {mount["Destination"]: mount["Source"] for mount in container["Mounts"]}
        return container["Image"] == self.record["image_id"] and actual.get(
            self.record["tls_target"]
        ) == str(generation)

    def _served(self, certificate: dict[str, Any]) -> bool:
        try:
            return served_fingerprint(("127.0.0.1", self.record["tls_port"])) == str(
                certificate["served_sha256"]
            )
        except (OSError, ServiceError):
            return False


class DockerSnapshots:
    def __init__(self, dependencies: tuple[Docker, DockerMaddy, Generations],
                 *, startup_timeout: float | None = None) -> None:
        self.docker, self.maddy, self.generations = dependencies
        self.prior_intent: dict[str, dict[str, Any]] = {}
        self.startup_timeout = startup_timeout

    def inventories(self) -> dict[str, list[str]]:
        consumers = self.generations.read()["consumers"].values()
        if any(
            consumer.get("intent") == "maintenance" and consumer.get("reason") == "offline_recovery"
            for consumer in consumers
        ):
            raise ServiceError("offline_recovery_in_progress", 409)
        self.maddy.validate()
        return self.maddy.inventories()

    def running(self) -> list[str]:
        state = self.generations.read()
        names = []
        for name, record in state["consumers"].items():
            if record["intent"] == "never_deployed":
                continue
            running = self.docker.inspect(name)["State"]["Running"]
            if running and record["intent"] != "expected_running":
                raise ServiceError("maintenance_consumer_unexpectedly_running", 409)
            if running:
                names.append(name)
        return names

    def pause(self, names: list[str]) -> None:
        state = self.generations.read()
        self.prior_intent = {name: dict(state["consumers"][name]) for name in names}
        for name in names:
            self.generations.maintenance(name, "backup_capture")
            self.docker.run(("stop", "--time", "30", name))
            if self.docker.inspect(name)["State"]["Running"]:
                raise ServiceError("consumer_stop_incomplete", 503)

    def resume(self, names: list[str]) -> None:
        failures = []
        for name in names:
            try:
                self._start_existing(name)
                if not self.docker.inspect(name)["State"]["Running"]:
                    raise ServiceError("consumer_resume_incomplete", 503)
                expected = self.prior_intent[name].get("served_sha256")
                if not isinstance(expected, str):
                    raise ServiceError("consumer_certificate_unverified", 503)
                self._verify_resumed(name, expected)
                state = self.generations.read()
                state["consumers"][name] = self.prior_intent[name]
                self.generations.write(state)
            except (OSError, ServiceError) as error:
                logging.getLogger(__name__).error(
                    'consumer_resume_failed consumer=%s code=%s', name,
                    error.code if isinstance(error, ServiceError) else type(error).__name__,
                )
                failures.append(name)
        if failures:
            raise ServiceError("backup_consumer_recovery_failed", 503)


    def _verify_resumed(self, name: str, expected: str) -> None:
        # Running is observable before the service begins accepting TLS connections.
        # Retry readiness within the existing verification budget, never extend it.
        deadline = time.monotonic() + (self.startup_timeout or self.docker.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceError("consumer_certificate_unverified", 503)
            try:
                verify_served(
                    ("127.0.0.1", 993 if name == "maddy" else 443), expected, remaining
                )
                return
            except (OSError, ServiceError):
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def _start_existing(self, name: str) -> None:
        """Starting a known container is idempotent; mailbox mutations never use this retry."""
        try:
            self.docker.run(("start", name))
        except ServiceError as error:
            state = self.docker.inspect(name)["State"]
            if state["Running"]:
                return
            cancelled = "context canceled" in str(state.get("Error", "")).lower()
            if (error.code not in {"command_completion_unknown", "command_deadline"}
                    and not cancelled) or state.get("Status") not in {"created", "exited"}:
                raise
            self.docker.run(("start", name))
