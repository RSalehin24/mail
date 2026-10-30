"""Root-only Trash observer and guarded native Maddy removal; no saved mailbox passwords."""

import argparse
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from backend.database import Database
from backend.webmail_schema import require_schema
from backend.webmail_schema import retire as retire_profile
from helper.maddy import DockerMaddy, cli_reported_error
from helper.process import CommandResult
from host.context import HostContext
from host.docker import DockerSnapshots
from shared.errors import ServiceError
from shared.files import protected_json
from shared.validation import address

RETENTION_SECONDS = 30 * 86400


def observe(store: Path) -> dict[str, dict[str, Any]]:
    if store.is_symlink() or not store.is_file():
        raise ServiceError("mailbox_store_missing", 503)
    # Read-only consistent snapshot. Never mutate the live imapsql database.
    with closing(sqlite3.connect(store.as_uri() + "?mode=ro", uri=True, timeout=1)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        result: dict[str, dict[str, Any]] = {}
        accounts = connection.execute(
            "SELECT u.id,u.username,b.uidvalidity FROM users u JOIN mboxes b "
            "ON b.id=u.inboxId AND b.uid=u.id"
        ).fetchall()
        for account in accounts:
            email = address(account["username"])
            folders = connection.execute(
                "SELECT id,name,uidvalidity FROM mboxes WHERE uid=? AND "
                "(lower(name)='trash' OR lower(specialuse)='\\trash')",
                (account["id"],),
            ).fetchall()
            entries = set()
            for folder in folders:
                for row in connection.execute(
                    "SELECT msgId FROM msgs WHERE mboxId=?", (folder["id"],)
                ):
                    entries.add((folder["name"], folder["uidvalidity"], row[0]))
                    if len(entries) > 100000:
                        raise ServiceError("trash_observation_limit_exceeded", 503)
            result[email] = {
                "native_id": account["id"],
                "inbox_validity": account["uidvalidity"],
                "entries": entries,
            }
        return result


class TrashRetention:
    def __init__(self, database: Database) -> None:
        self.database = database
        require_schema(database)

    def reconcile(
        self, observed: dict[str, dict[str, Any]], now: float, reset: bool = False
    ) -> list[dict[str, Any]]:
        due = []
        with self.database.transaction() as connection:
            enabled = connection.execute(
                "SELECT cleanup_enabled FROM webmail_maintenance"
            ).fetchone()[0]
            users = connection.execute(
                "SELECT id,email,inbox_validity FROM mailbox_users WHERE active=1"
            ).fetchall()
            if reset:
                connection.execute("DELETE FROM mailbox_trash")
                connection.execute("DELETE FROM mailbox_permanent_deletions")
            for user in users:
                mailbox = observed.get(user["email"])
                if mailbox is None or mailbox["inbox_validity"] != user["inbox_validity"]:
                    # No cleanup through a changed or deleted/recreated identity.
                    if reset:
                        raise ServiceError("trash_identity_reconciliation_required", 409)
                    continue
                known = connection.execute(
                    "SELECT * FROM mailbox_trash WHERE user_id=?", (user["id"],)
                ).fetchall()
                for row in known:
                    key = (row["folder"], row["uidvalidity"], row["uid"])
                    if key not in mailbox["entries"]:
                        connection.execute(
                            "DELETE FROM mailbox_trash WHERE user_id=? AND folder=? "
                            "AND uidvalidity=? AND uid=?",
                            (user["id"], *key),
                        )
                        connection.execute(
                            "DELETE FROM mailbox_permanent_deletions WHERE "
                            "user_id=? AND folder=? AND uidvalidity=? AND uid=?",
                            (user["id"], *key),
                        )
                    elif (
                        enabled
                        and not reset
                        and (
                            row["entered"] <= now - RETENTION_SECONDS
                            or connection.execute(
                                "SELECT 1 FROM mailbox_permanent_deletions WHERE "
                                "user_id=? AND folder=? AND uidvalidity=? AND uid=?",
                                (user["id"], *key),
                            ).fetchone()
                        )
                    ):
                        due.append(
                            {
                                **dict(row),
                                "email": user["email"],
                                "inbox_validity": user["inbox_validity"],
                                "native_id": mailbox["native_id"],
                            }
                        )
                for folder, validity, uid in mailbox["entries"]:
                    connection.execute(
                        "INSERT OR IGNORE INTO mailbox_trash VALUES(?,?,?,?,?,NULL)",
                        (user["id"], folder, validity, uid, now),
                    )
            if reset:
                connection.execute(
                    "UPDATE webmail_maintenance SET cleanup_enabled=1,reconciled=?", (now,)
                )
        return due[:100]

    @staticmethod
    def validate_removal(item: dict[str, Any], current: dict[str, dict[str, Any]]) -> None:
        mailbox = current.get(item["email"])
        if (
            mailbox is None
            or mailbox["native_id"] != item["native_id"]
            or mailbox["inbox_validity"] != item["inbox_validity"]
            or (item["folder"], item["uidvalidity"], item["uid"]) not in mailbox["entries"]
        ):
            raise ServiceError("trash_identity_changed", 409)


def run(
    context: HostContext,
    reconcile: bool = False,
    retain_profiles: tuple[str, ...] = (),
    new_users: tuple[str, ...] = (),
) -> dict[str, Any]:
    deployment = context.deployment
    marker = protected_json(Path("/etc/mail-admin/installation.json"))
    database = Database(
        Path(deployment["backup_sources"]["dashboard"]) / "admin_data.db", marker["installation_id"]
    )
    retention = TrashRetention(database)
    store = Path(deployment["maddy_data_directory"]) / "imapsql.db"
    maddy = DockerMaddy(deployment)
    snapshots = DockerSnapshots((context.docker, maddy, context.generations))
    removed = 0
    with context.coordination.consistent():
        maddy.validate()
        observed = observe(store)
        if retain_profiles or new_users:
            if not reconcile or context.docker.inspect("mail-admin")["State"]["Running"]:
                raise ServiceError("webmail_identity_maintenance_required", 409)
            with database.transaction() as connection:
                for email in (*retain_profiles, *new_users):
                    if address(email) not in observed:
                        raise ServiceError("account_not_found", 404)
                for email in new_users:
                    retire_profile(connection, address(email))
                for email in retain_profiles:
                    email = address(email)
                    connection.execute(
                        "UPDATE mailbox_users SET inbox_validity=?,revision=revision+1 "
                        "WHERE email=? AND active=1",
                        (observed[email]["inbox_validity"], email),
                    )
                    connection.execute(
                        "DELETE FROM mailbox_sessions WHERE user_id IN "
                        "(SELECT id FROM mailbox_users WHERE email=?)",
                        (email,),
                    )
        due = retention.reconcile(observed, time.time(), reconcile)
        if due:
            if "maddy" not in snapshots.running():
                raise ServiceError("maddy_maintenance_unavailable", 503)
            # The stock 0.9.6 CLI has no atomic UIDVALIDITY precondition. Quiesce
            # writers only when deletions are due, then revalidate before each
            # native removal. Never race an external IMAP move/recreated folder.
            snapshots.pause(["maddy"])
            try:
                for item in due:
                    if context.docker.inspect("maddy")["State"]["Running"]:
                        raise ServiceError("trash_writer_exclusion_lost", 409)
                    retention.validate_removal(item, observe(store))
                    output = context.docker.run(
                        (
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--read-only",
                            "--cap-drop",
                            "ALL",
                            "--security-opt",
                            "no-new-privileges:true",
                            "--memory",
                            "96m",
                            "--pids-limit",
                            "64",
                            "--volumes-from",
                            "maddy",
                            "--tmpfs",
                            "/run/maddy:size=1048576,mode=0700",
                            "--entrypoint",
                            "/usr/local/libexec/mail-admin-maddy",
                            deployment["inputs"]["maddy_image_id"],
                            "imap-msgs",
                            "remove",
                            "--uid",
                            "--yes",
                            address(item["email"]),
                            item["folder"],
                            str(item["uid"]),
                        )
                    )
                    if cli_reported_error(CommandResult(0, output)):
                        raise ServiceError("trash_native_removal_uncertain", 503)
                    if (item["folder"], item["uidvalidity"], item["uid"]) in observe(store).get(
                        item["email"], {}
                    ).get("entries", set()):
                        raise ServiceError("trash_native_removal_unverified", 503)
                    with database.transaction() as connection:
                        connection.execute(
                            "DELETE FROM mailbox_permanent_deletions WHERE user_id=? "
                            "AND folder=? AND uidvalidity=? AND uid=?",
                            (item["user_id"], item["folder"], item["uidvalidity"], item["uid"]),
                        )
                        connection.execute(
                            "DELETE FROM mailbox_trash WHERE user_id=? AND folder=? "
                            "AND uidvalidity=? AND uid=?",
                            (item["user_id"], item["folder"], item["uidvalidity"], item["uid"]),
                        )
                    removed += 1
            finally:
                snapshots.resume(["maddy"])
    return {"observed": True, "removed": removed, "reconciled": reconcile}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Verify restored references, restart retention clocks and enable cleanup",
    )
    parser.add_argument(
        "--retain-profile",
        action="append",
        default=[],
        metavar="MAILBOX",
        help="With runtime stopped, reconnect a verified same-owner mailbox",
    )
    parser.add_argument(
        "--new-user",
        action="append",
        default=[],
        metavar="MAILBOX",
        help="With runtime stopped, retire metadata for a verified recreated account",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    if set(map(address, args.retain_profile)) & set(map(address, args.new_user)):
        parser.error(
            "A mailbox cannot retain a profile and become a new user in the same operation"
        )
    run(
        HostContext.load(),
        args.reconcile,
        tuple(map(address, args.retain_profile)),
        tuple(map(address, args.new_user)),
    )


if __name__ == "__main__":
    main()
