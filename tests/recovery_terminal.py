"""Drive the actual isolated operator utility through a private, never-logged terminal."""

import errno
import os
import pty
import re
import select
import signal
import time
from dataclasses import dataclass

from tests.fixture_values import PASSWORD
from tests.host_factor_support import totp


@dataclass(frozen=True, repr=False)
class RecoveryMaterial:
    seed: str
    codes: tuple[str, ...]


class PrivateTerminal:
    def __init__(self, descriptor, password=PASSWORD):
        self.descriptor, self.pending = descriptor, b""
        self.password = password
        self.deadline = time.monotonic() + 90
        self.password_echoed = False

    def expect(self, marker):
        while marker not in self.pending:
            self._read()
        value, self.pending = self.pending.split(marker, 1)
        return value

    def _read(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.descriptor], [], [], remaining)[0]:
            raise RuntimeError("Isolated recovery terminal deadline exceeded")
        try:
            data = os.read(self.descriptor, 4096)
        except OSError as error:
            if error.errno != errno.EIO:
                raise
            data = b""
        if not data:
            raise RuntimeError("Isolated recovery terminal closed before confirmation")
        self.pending += data.replace(b"\r", b"")
        self.password_echoed |= self.password.encode() in self.pending
        if len(self.pending) > 65536:
            raise RuntimeError("Isolated recovery terminal output exceeded its bound")

    def send(self, value):
        os.write(self.descriptor, value.encode() + b"\n")

    def credentials(self):
        self.expect(b"New master password: ")
        self.send(self.password)
        self.expect(b"Confirm master password: ")
        self.send(self.password)
        self.expect(b"Enroll this replacement TOTP secret in your authenticator: ")
        seed = self.expect(b"\n").decode().strip()
        if not re.fullmatch(r"[A-Z2-7]{32,128}", seed):
            raise RuntimeError("Recovery utility returned an invalid fixture factor")
        self.expect(b"New authenticator code: ")
        self.send(self.next_step_code(seed))
        self.expect(b"Save these five single-use recovery codes now:\n")
        codes = tuple(self.expect(b"\n").decode().strip() for _ in range(5))
        if any(not re.fullmatch(r"[0-9a-f]{32}", code) for code in codes):
            raise RuntimeError("Recovery utility returned invalid fixture recovery codes")
        if self.password_echoed:
            raise RuntimeError("Recovery utility echoed its protected password input")
        return RecoveryMaterial(seed, codes)

    def next_step_code(self, seed):
        # Wait inside the existing terminal deadline so subsequent factor tests have a fresh step.
        previous = int(time.time() // 30)
        while int(time.time() // 30) <= previous:
            if time.monotonic() >= self.deadline:
                raise RuntimeError('Recovery factor did not advance within the terminal deadline')
            time.sleep(0.1)
        return totp(seed, time.time())


def recover_through_terminal(recovery, tools, destination):
    return TerminalRecovery(recovery, tools).recover(destination, PASSWORD)


class TerminalRecovery:
    def __init__(self, recovery, tools):
        self.recovery, self.tools = recovery, tools

    def recover(self, destination, password):
        identifier, descriptor = pty.fork()
        if identifier == 0:
            _child_recovery(self.recovery, self.tools, destination)
        try:
            terminal = PrivateTerminal(descriptor, password)
            material = terminal.credentials()
            _require_exit(identifier, terminal.deadline)
            return material
        finally:
            os.close(descriptor)
            _terminate_child(identifier)


def _child_recovery(recovery, tools, destination):
    try:
        recovery.recover_authentication(destination, tools)
    except BaseException as error:
        print("Isolated recovery failed:", type(error).__name__, flush=True)
        os._exit(1)
    os._exit(0)


def _require_exit(identifier, deadline):
    while time.monotonic() < deadline:
        observed, status = os.waitpid(identifier, os.WNOHANG)
        if observed:
            if os.waitstatus_to_exitcode(status) != 0:
                raise RuntimeError("Isolated recovery operator utility failed")
            return
        time.sleep(0.02)
    raise RuntimeError("Isolated recovery completion deadline exceeded")


def _terminate_child(identifier):
    try:
        observed, _ = os.waitpid(identifier, os.WNOHANG)
    except ChildProcessError:
        return
    if not observed:
        os.kill(identifier, signal.SIGTERM)
        os.waitpid(identifier, 0)
