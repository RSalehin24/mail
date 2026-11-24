"""Owner command: restore mail data from a backup, run as root on the VM.

    sudo python3 -m host.restore_backup --list
    sudo python3 -m host.restore_backup <snapshot-id>
    sudo python3 -m host.restore_backup --reconcile

Messages and folders missing from the live mailboxes are merged in; newer mail and existing
passwords are never overwritten. Mailboxes that no longer exist are recreated and the command asks
for a new password for each. Everything runs in this process, nothing waits on a queue.
"""

import argparse
import getpass
import os
import re
import shutil
import signal
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from host.backup_control import BackupWorker, interrupted
from host.context import HostContext
from shared.errors import ServiceError
from shared.validation import mailbox_password

SNAPSHOT_PATTERN = re.compile(r"[0-9a-f]{64}")
LISTING_PAGE_SIZE = 50
DHAKA = timezone(timedelta(hours=6), "Asia/Dhaka")


class Console:
    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt)


def dhaka(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, DHAKA).strftime("%Y-%m-%d %H:%M Dhaka")


class RestoreCommand:
    def __init__(
        self, worker: BackupWorker, console: Console, sync_forwarding: Callable[[], None]
    ) -> None:
        self.worker, self.console, self.sync_forwarding = worker, console, sync_forwarding

    def list_snapshots(self) -> None:
        with self.worker.exclusive():
            self.worker.catalog.refresh()
        listing = self.worker.control.listing(
            {"kind": "snapshots", "page": 1, "page_size": LISTING_PAGE_SIZE}
        )
        for snapshot in listing["items"]:
            verified = (snapshot.get("verification") or {}).get("verified_at")
            state = "verified " + dhaka(verified) if verified else "not yet verified"
            self.console.say(f"{snapshot['id']}  {dhaka(snapshot['cutoff'])}  {state}")
        if listing["total"] > len(listing["items"]):
            self.console.say(f"Showing the newest {len(listing['items'])} of {listing['total']}.")

    def restore(self, snapshot: str) -> int:
        if not SNAPSHOT_PATTERN.fullmatch(snapshot):
            raise ServiceError("exact_snapshot_id_required", 400)
        prepared = self._preflight(snapshot)
        report = prepared["result"]
        self._report(report)
        try:
            passwords = {email: self._new_password(email) for email in report["needs_login"]}
            typed = self.console.ask("Type the snapshot id to restore it: ").strip()
        except (EOFError, KeyboardInterrupt):
            return self._decline(prepared, "Cancelled; nothing was changed.")
        if typed != snapshot:
            return self._decline(prepared, "The snapshot id does not match; nothing was changed.")
        with self.worker.exclusive():
            identifier = self.worker.submit("restore", snapshot, prepared["id"])
            job = self.worker.process(identifier, passwords)
        self.console.say(f"Restored {job['result'].get('messages_added', 0)} messages.")
        self._sync_forwarding()
        return 0

    def _decline(self, prepared: dict[str, Any], message: str) -> int:
        # The checked copy is only needed to restore it; without a restore it is just 200 MB.
        shutil.rmtree(self.worker.staging / prepared["id"], ignore_errors=True)
        self.console.say(message)
        return 2

    def reconcile(self) -> int:
        with self.worker.exclusive():
            with self.worker.control.connect() as connection:
                rows = connection.execute(
                    "SELECT id FROM jobs WHERE state='reconciliation_required' ORDER BY created"
                ).fetchall()
            if not rows:
                self.console.say("Nothing needs reconciling.")
                return 0
            for row in rows:
                self.console.say(f"Reconciling operation {row['id']}.")
                job = self.worker.process(self.worker.submit("reconcile", "", row["id"]))
                outcome = "committed" if job["result"]["committed"] else "rolled back"
                self.console.say(f"Operation {outcome}.")
        return 0

    def _preflight(self, snapshot: str) -> dict[str, Any]:
        with self.worker.exclusive():
            self.worker.catalog.refresh()
            self.console.say("Restoring the snapshot to staging and checking it...")
            return self.worker.process(self.worker.submit("preflight", snapshot))

    def _report(self, report: dict[str, Any]) -> None:
        present = sorted(set(report["mailboxes"]) - set(report["missing"]))
        self.console.say("Mailboxes that exist and get missing mail merged in:")
        for email in present:
            self.console.say(f"  {email}")
        self.console.say("Mailboxes that are missing and will be recreated:")
        for email in report["missing"]:
            self.console.say(f"  {email}")
        self.console.say("A verified safety backup is taken before any live change.")

    def _new_password(self, email: str) -> str:
        while True:
            password = self.console.secret(f"New password for {email}: ")
            try:
                mailbox_password(password)
            except ServiceError:
                self.console.say("Use 16 characters or more and at most 72 UTF-8 bytes.")
                continue
            if password == self.console.secret("Repeat it: "):
                return password
            self.console.say("The passwords differ.")

    def _sync_forwarding(self) -> None:
        try:
            self.sync_forwarding()
        except (OSError, ServiceError) as error:
            self.console.say(
                f"Mail is restored, but applying forwarding failed ({error}). "
                "Run: sudo python3 -m host.sync_forwarding"
            )
            return
        self.console.say("Forwarding applied for the restored mailboxes.")


def parse(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("snapshot", nargs="?", help="the exact snapshot id to restore")
    parser.add_argument("--list", action="store_true", help="list the backups and exit")
    parser.add_argument("--reconcile", action="store_true", help="settle an interrupted restore")
    options = parser.parse_args(arguments)
    if [options.list, options.reconcile, bool(options.snapshot)].count(True) != 1:
        parser.error("give a snapshot id, or --list, or --reconcile")
    return options


def run(options: argparse.Namespace) -> int:
    from host.sync_forwarding import sync_forwarding

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    command = RestoreCommand(BackupWorker(HostContext.load()), Console(), sync_forwarding)
    if options.list:
        command.list_snapshots()
        return 0
    return command.reconcile() if options.reconcile else command.restore(options.snapshot)


def main(arguments: list[str]) -> int:
    options = parse(arguments)
    if os.geteuid() != 0:
        print("Run this as root: sudo python3 -m host.restore_backup", file=sys.stderr)
        return 1
    try:
        return run(options)
    except (OSError, ServiceError) as error:
        print(f"restore failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
