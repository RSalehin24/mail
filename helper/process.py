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
    merge_stderr: bool = True


@dataclass(frozen=True)
class CommandResult:
    code: int
    output: str
    error_output: str = ""


def execute(command: Command) -> CommandResult:
    # Only host code constructs Command; untrusted requests never supply argv or executables.
    process = subprocess.Popen(  # noqa: S603 -- fixed argv is built only by trusted host adapters.
        command.arguments,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if command.merge_stderr else subprocess.PIPE,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", **(command.environment or {})},
        close_fds=True,
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise ServiceError("command_channel_failed", 503)
        process.stdin.write(command.input_bytes)
        process.stdin.close()
        if command.merge_stderr:
            return _collect(process, command.timeout)
        return _collect(process, command.timeout, True)
    except (OSError, ServiceError, subprocess.TimeoutExpired) as error:
        process.kill()
        process.wait(timeout=2)
        raise ServiceError("command_completion_unknown", 503) from error
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _collect(
    process: subprocess.Popen[bytes], timeout: float, separate_stderr: bool = False,
) -> CommandResult:
    if process.stdout is None:
        raise ServiceError("command_channel_failed", 503)
    deadline, output = time.monotonic() + timeout, bytearray()
    errors = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ, output)
        if separate_stderr:
            if process.stderr is None:
                raise ServiceError("command_channel_failed", 503)
            selector.register(process.stderr, selectors.EVENT_READ, errors)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ServiceError("command_deadline", 503)
            for key, _ in selector.select(min(remaining, 0.05)):
                chunk = os.read(key.fd, 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                key.data.extend(chunk)
                if len(output) + len(errors) > 65536:
                    raise ServiceError("command_output_limit", 503)
        return CommandResult(
            process.wait(timeout=max(0.01, deadline - time.monotonic())),
            output.decode("utf-8", errors="replace"),
            errors.decode("utf-8", errors="replace"),
        )
