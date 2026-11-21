"""Root-only Docker lifecycle adapters. No web/helper operation exposes recreation."""

import json
import logging
import time
from typing import Any

from helper.maddy import DockerMaddy
from helper.process import Command, execute
from host.certificates import served_fingerprint, verify_served
from host.consumers import ConsumerIntents
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


class DockerSnapshots:
    def __init__(self, dependencies: tuple[Docker, DockerMaddy, ConsumerIntents],
                 *, startup_timeout: float | None = None) -> None:
        self.docker, self.maddy, self.intents = dependencies
        self.prior_intent: dict[str, dict[str, Any]] = {}
        self.startup_timeout = startup_timeout

    def inventories(self) -> dict[str, list[str]]:
        consumers = self.intents.read()["consumers"].values()
        if any(
            consumer.get("intent") == "maintenance" and consumer.get("reason") == "offline_recovery"
            for consumer in consumers
        ):
            raise ServiceError("offline_recovery_in_progress", 409)
        self.maddy.validate()
        return self.maddy.inventories()

    def running(self) -> list[str]:
        state = self.intents.read()
        names = []
        for name, record in state["consumers"].items():
            running = self.docker.inspect(name)["State"]["Running"]
            if running and record["intent"] != "expected_running":
                raise ServiceError("maintenance_consumer_unexpectedly_running", 409)
            if running:
                names.append(name)
        return names

    def pause(self, names: list[str]) -> None:
        state = self.intents.read()
        # Maddy renews its own certificate, so resuming must see what was served just before.
        self.prior_intent = {
            name: {**state["consumers"][name],
                   "served_sha256": served_fingerprint(("127.0.0.1", self._tls_port(name)))}
            for name in names
        }
        for name in names:
            self.intents.maintenance(
                name, "backup_capture", self.prior_intent[name]["served_sha256"]
            )
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
                state = self.intents.read()
                state["consumers"][name] = self.prior_intent[name]
                self.intents.write(state)
            except (OSError, ServiceError) as error:
                logging.getLogger(__name__).error(
                    'consumer_resume_failed consumer=%s code=%s', name,
                    error.code if isinstance(error, ServiceError) else type(error).__name__,
                )
                failures.append(name)
        if failures:
            raise ServiceError("backup_consumer_recovery_failed", 503)


    @staticmethod
    def _tls_port(name: str) -> int:
        return 993 if name == "maddy" else 443

    def _verify_resumed(self, name: str, expected: str) -> None:
        # Running is observable before the service begins accepting TLS connections.
        # Retry readiness within the existing verification budget, never extend it.
        deadline = time.monotonic() + (self.startup_timeout or self.docker.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceError("consumer_certificate_unverified", 503)
            try:
                verify_served(("127.0.0.1", self._tls_port(name)), expected, remaining)
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
