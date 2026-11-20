"""Kill the dashboard right after a real Maddy command, before it can record the result."""

import hashlib
import http.client
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from shared.errors import ServiceError
from tests.runtime_support import RuntimeFixture

MADDY_TARGET = "/usr/local/bin/maddy"
REAL_MADDY_TARGET = "/usr/local/bin/maddy-real"
CALL_LOG = "cli-calls.log"
HOLD_FILE = "hold-after"
HELD_FILE = "held"
# Every call is logged; the process then stalls after the chosen subcommand has really run,
# which is the window where a crash loses the result but not the mutation.
WRAPPER = f"""#!/bin/sh
echo "$*" >> /data/{CALL_LOG}
{REAL_MADDY_TARGET} "$@"
status=$?
if [ -e /data/{HOLD_FILE} ] && [ "$3 $4" = "$(cat /data/{HOLD_FILE})" ]; then
  : > /data/{HELD_FILE}
  exec sleep 600
fi
exit $status
"""


class CrashRuntime(RuntimeFixture):
    """The runtime image with a logging Maddy wrapper that can stall after one command."""

    def __init__(self, maddy, deployment_inputs=None):
        super().__init__(maddy, deployment_inputs)
        self.wrapper = self.root / "maddy-wrapper"
        self.wrapper.write_text(WRAPPER)
        self.wrapper.chmod(0o755)
        self.real = self.root / "maddy-real"
        self.extract_maddy_binary()

    def extract_maddy_binary(self):
        container = self.command(("create", os.environ["MAIL_ADMIN_RUNTIME_REFERENCE"])).stdout
        try:
            self.command(("cp", container.strip() + ":" + MADDY_TARGET, str(self.real)))
        finally:
            self.command(("rm", container.strip()))

    def host_sources(self):
        return super().host_sources() + (
            (self.wrapper, MADDY_TARGET),
            (self.real, REAL_MADDY_TARGET),
        )

    def hold_after(self, subcommand):
        (self.maddy_files.data / HOLD_FILE).write_text(subcommand)

    def clear_hold(self):
        for name in (HELD_FILE, HOLD_FILE):
            (self.maddy_files.data / name).unlink(missing_ok=True)

    def held(self):
        return (self.maddy_files.data / HELD_FILE).exists()

    def calls(self, subcommand):
        """Argument lists of the Maddy CLI calls that started with the given subcommand."""
        log = self.maddy_files.data / CALL_LOG
        lines = log.read_text().splitlines() if log.exists() else []
        return [line for line in lines if " ".join(line.split()[2:4]) == subcommand]


class DeletionEnabledRuntime(CrashRuntime):
    """Permanent deletion on, with root-style records that authorize one backed-up mailbox."""

    def __init__(self, maddy):
        configuration = maddy.configuration.read_bytes()
        policy = {
            "valid_until": time.time() + 3600,
            "protected_recipients": [],
            "reviewed_files": {"/data/maddy.conf": hashlib.sha256(configuration).hexdigest()},
        }
        digest = hashlib.sha256(json.dumps(policy).encode()).hexdigest()
        super().__init__(maddy, {"permanent_deletion_enabled": True,
                                 "protected_policy_sha256": digest,
                                 "deletion_backup_max_age_seconds": 600})
        self.write_record("helper-policy.json", policy)

    def command(self, arguments):
        if arguments[0] == "create":
            arguments = arguments[:-1] + ("-e", "PERMANENT_DELETION_ENABLED=true", arguments[-1])
        return super().command(arguments)

    def authorize(self, email):
        """Record a fresh verified backup covering this mailbox; return the cutoff to send."""
        cutoff = datetime.now(UTC).isoformat()
        generation = self.journal().incarnation(email)["generation"]
        covered = {"generation": generation, "credentials": True, "storage": True,
                   "messages": True}
        self.write_record("backup-manifest.json", {
            "verified": True, "available": True, "restore_passed": True,
            "availability_observed_at": time.time(), "cutoff": cutoff,
            "accounts": {email: covered},
        })
        return cutoff


def kill_pending_api(assertions, web, request):
    """Send the request, wait until its Maddy command has run, then SIGKILL the dashboard."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(web.api, *request)
        deadline = time.monotonic() + 10
        while not web.held() and time.monotonic() < deadline:
            time.sleep(0.05)
        assertions.assertTrue(web.held())
        assertions.assertFalse(future.done())
        web.command(("kill", "--signal", "KILL", web.identifier))
        state = web.exited()
        assertions.assertEqual(state["ExitCode"], 137)
        assertions.assertFalse(state["OOMKilled"])
        with assertions.assertRaises((OSError, http.client.HTTPException)):
            future.result(timeout=10)
    web.clear_hold()



def wait_for_complete(journal, identifier):
    """Poll the dashboard's journal; a write lock held by the live process is only a retry."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            record = journal.lookup(identifier)
        except ServiceError as error:
            cause = error.__cause__
            if (error.code != 'journal_unavailable'
                    or not isinstance(cause, sqlite3.OperationalError)
                    or getattr(cause, 'sqlite_errorcode', None)
                    not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}):
                raise
            record = None
        if record and record['state'] != 'pending':
            return record
        time.sleep(0.1)
    raise RuntimeError('The retained helper operation did not complete')
