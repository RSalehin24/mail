"""Bound subprocess time/output without shell interpolation or password argv."""

import os
import selectors
import subprocess
import time
from dataclasses import dataclass

from shared.errors import ServiceError


@dataclass(frozen=True)
class Command:
    arguments: tuple[str, ...]
    input_bytes: bytes = b""
    timeout: float = 10
    environment: dict[str, str] | None = None


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str


def execute(command: Command) -> CommandResult:
    # Only host code constructs Command; untrusted requests never supply argv or executables.
    process = subprocess.Popen(  # noqa: S603 -- fixed argv is built only by trusted host adapters.
        command.arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", **(command.environment or {})},
        close_fds=True,
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise ServiceError("command_channel_failed", 503)
        process.stdin.write(command.input_bytes)
        process.stdin.close()
        return _collect(process, command.timeout)
    except (OSError, ServiceError, subprocess.TimeoutExpired) as error:
        process.kill()
        process.wait(timeout=2)
        raise ServiceError("command_completion_unknown", 503) from error
    finally:
        if process.stdout is not None:
            process.stdout.close()


def _collect(process: subprocess.Popen[bytes], timeout: float) -> CommandResult:
    if process.stdout is None:
        raise ServiceError("command_channel_failed", 503)
    deadline, output = time.monotonic() + timeout, bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceError("command_deadline", 503)
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                output.extend(chunk)
                if len(output) > 65536:
                    raise ServiceError("command_output_limit", 503)
        return CommandResult(
            process.wait(timeout=max(0.01, deadline - time.monotonic())),
            output.decode("utf-8", errors="replace"),
        )
