"""Fixed Docker/Maddy argv and host-side identity/mount/database preflight."""

import json
import re
from pathlib import Path
from typing import Any, Protocol

from helper.process import Command, CommandResult, execute
from shared.errors import ServiceError
from shared.files import protected_json
from shared.validation import address

LAUNCHER = "/usr/local/libexec/mail-admin-maddy"


def cli_reported_error(response: CommandResult) -> bool:
    # The pinned 0.9.5 CLI can log app.Run failure and still exit zero.
    # Password prompts need not end with a newline before that diagnostic.
    return (re.search(r"(?<!\S)app\.Run failed(?=\s|$)", response.output) is not None
            or response.output.startswith('Failed to initialize update pipe, '))


def cli_succeeded(response: CommandResult) -> bool:
    return response.code == 0 and not cli_reported_error(response)


def provisioning_warning(response: CommandResult) -> bool:
    return "warning" in response.output.lower() or bool(
        re.search(r"Failed to create (?:sent|trash|junk|drafts|archive) folder:", response.output)
    )


class MaddyAccess(Protocol):
    def validate(self) -> None: ...
    def inventories(self) -> dict[str, list[str]]: ...
    def change(self, arguments: tuple[str, ...], password: str = "") -> CommandResult: ...


class DockerMaddy:
    def __init__(self, deployment: dict[str, Any], executable: str = "/usr/bin/docker") -> None:
        self.deployment = deployment
        self.executable = executable
        self.timeout = float(deployment["inputs"]["operation_timeout_seconds"])

    def _docker(self, arguments: tuple[str, ...]) -> CommandResult:
        result = execute(Command((self.executable,) + arguments, timeout=self.timeout))
        if result.code:
            raise ServiceError("maddy_unavailable", 503)
        return result

    def validate(self) -> None:
        container = json.loads(self._docker(("inspect", "maddy")).output)[0]
        expected = self.deployment["inputs"]["maddy_image_id"]
        image = json.loads(self._docker(("image", "inspect", expected)).output)[0]
        if (
            container["Image"] != expected
            or image["Architecture"] != "amd64"
            or image["Os"] != "linux"
        ):
            raise ServiceError("maddy_identity_mismatch", 503)
        expected_mounts = self.expected_mounts()
        actual = {mount["Destination"]: mount["Source"] for mount in container["Mounts"]}
        if actual != expected_mounts:
            raise ServiceError("maddy_mount_mismatch", 503)
        data = Path(self.deployment["maddy_data_directory"])
        if any(
            not (data / name).is_file() or (data / name).is_symlink()
            for name in ("credentials.db", "imapsql.db")
        ):
            raise ServiceError("maddy_storage_missing", 503)
        if not container["State"]["Running"]:
            raise ServiceError("maddy_unavailable", 503)
        self.validate_cli_version()

    def expected_cli_version(self) -> str:
        version = self.deployment.get('maddy_cli_version', '0.9.5')
        if version not in ('0.9.5', '0.9.6'):
            raise ServiceError('maddy_version_unsupported', 503)
        return str(version)

    def validate_cli_version(self) -> None:
        result = self.change(("version",))
        pattern = r'(?<![0-9.])' + re.escape(self.expected_cli_version()) + r'(?=$|\s)'
        if not cli_succeeded(result) or not re.search(pattern, result.output):
            raise ServiceError("maddy_version_mismatch", 503)

    def expected_mounts(self) -> dict[str, str]:
        expected = dict(self.deployment["maddy_mounts"])
        state_path = self.deployment.get("certificate_state")
        if state_path is None:
            return expected
        state = protected_json(Path(state_path))
        if state.get("coordination_migrated") is not True:
            return expected
        generation = state.get("consumers", {}).get("maddy", {}).get("active_generation")
        if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{64}", generation):
            raise ServiceError("maddy_generation_unknown", 503)
        target = self.deployment["maddy_tls_target"]
        expected[target] = str(Path(self.deployment["tls_generations_directory"]) / generation)
        return expected

    def inventories(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for key, command in (("credentials", "creds"), ("storage", "imap-acct")):
            response = self.change((command, "list"))
            if not cli_succeeded(response):
                raise ServiceError("inventory_unavailable", 503)
            if response.output.strip() == "No users.":
                result[key] = []
                continue
            result[key] = sorted(
                address(line.strip()) for line in response.output.splitlines() if line.strip()
            )
        return result

    def change(self, arguments: tuple[str, ...], password: str = "") -> CommandResult:
        prefix = (self.executable, "exec", "-i", "maddy", LAUNCHER)
        payload = (password + "\n" + password + "\n").encode() if password else b""
        return execute(Command(prefix + arguments, payload, self.timeout))
