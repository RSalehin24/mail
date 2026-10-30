"""Independent encrypted backups and non-destructive restoration into a new directory."""

import argparse
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from applications.schema import install
from backend.database import Database
from backend.webmail_schema import invalidate_restored
from shared.errors import ServiceError
from shared.files import atomic_group_json, atomic_json, protected_json


def invalidate(database: Database) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM sessions")
        connection.execute("DELETE FROM auth_challenges")
        connection.execute("DELETE FROM otp_codes")
        connection.execute("UPDATE admin_auth SET revision=revision+1,pending_secret=NULL")
        invalidate_restored(connection)
        connection.execute("UPDATE integration_credentials SET revoked=1")
        connection.execute("DELETE FROM integration_enrollments")
        connection.execute("DELETE FROM remote_pairing")
        connection.execute("UPDATE managed_mailbox_identities SET revision=revision+1")
        connection.execute("DELETE FROM trash_outbox")
        connection.execute("DELETE FROM integration_trash_events")
        connection.execute("DELETE FROM managed_trash")
        connection.execute(
            "UPDATE application_state SET cleanup_enabled=0,recovery_epoch=?",
            (str(uuid.uuid4()),),
        )
        # Restored mailbox sessions and uncertain sending operations require manual review.
        row = connection.execute("SELECT settings FROM application_state").fetchone()
        values = json.loads(row[0])
        values.update(webmail_enabled=False, allowlist=[], pilot_verified=False)
        connection.execute("UPDATE application_state SET settings=?", (json.dumps(values),))


def backup(host: dict[str, Any]) -> dict[str, Any]:
    from applications.host import command

    root = Path(host["root"])
    os.environ["MAIL_CONFIG"] = str(root / "site.json")
    marker = json.loads((root / "installation.json").read_text())
    database = Database(root / (host["kind"] + ".db"), marker["installation_id"])
    with database.transaction() as connection:
        values = json.loads(
            connection.execute("SELECT settings FROM application_state").fetchone()[0]
        )
    repository = Path(values["backup_destination"])
    allowed = Path("/var/backups/mail-" + host["kind"])
    if not repository.resolve().is_relative_to(allowed) or any(
        item.is_symlink() for item in (allowed, *repository.parents)
    ):
        raise ServiceError("unsafe_backup_destination", 400)
    from host.locks import HostLock, LockPolicy

    lock = Path(
        "/run/lock/mail-admin-maddy.lock"
        if host.get("adopted")
        else "/run/lock/mail-" + host["kind"] + ".lock"
    )
    with HostLock(lock, LockPolicy(2)):
        compose = "/etc/mail-" + host["kind"] + "/compose.json"
        password = Path("/etc/mail-" + host["kind"] + "/backup.key")
        if not password.exists():
            raise ServiceError("backup_recovery_key_required", 503)
        restic = str(Path(host["release"]) / "restic")
        arguments = (
            restic,
            "--no-cache",
            "--repo",
            str(repository),
            "--password-file",
            str(password),
        )
        if not (repository / "config").exists():
            command(*arguments, "init")
        was_maddy = host["kind"] == "management"
        identifier = uuid.uuid4().hex
        metadata = {
            "id": identifier,
            "created": time.time(),
            "installation_id": marker["installation_id"],
            "kind": host["kind"],
            "version": host["version"],
            "sessions_valid_after_restore": False,
            "repair_required": ["re-pair", "identities", "message references", "Trash clocks"],
            "host": host,
        }
        apps_stopped, maddy_stopped = False, False
        try:
            command("/usr/bin/docker", "compose", "-f", compose, "stop")
            apps_stopped = True
            if was_maddy:
                command("/usr/bin/docker", "stop", "--time", "10", "maddy")
                maddy_stopped = True
            sources = [str(root)]
            if was_maddy:
                from applications.host import deployment
                from helper.maddy import DockerMaddy
                from host.webmail_retention import observe

                native_configuration = deployment(host)
                native = DockerMaddy(native_configuration)
                accounts = observe(
                    Path(native_configuration["maddy_data_directory"]) / "imapsql.db"
                )
                metadata["accounts"] = {email: native.identity(email) for email in accounts}

                sources += [
                    deployment(host)["maddy_data_directory"],
                    "/var/lib/mail-management-helper",
                ]
            atomic_json(root / "recovery.json", metadata)
            command(
                *arguments, "backup", "--tag", identifier, "--exclude", ".webmail-spool", *sources
            )
            command(*arguments, "check")
            # Keep a week of daily snapshots; an interrupted expiration never deletes live mail.
            command(*arguments, "forget", "--keep-within", "7d")
        finally:
            try:
                if maddy_stopped:
                    command("/usr/bin/docker", "start", "maddy")
            finally:
                if apps_stopped:
                    command("/usr/bin/docker", "compose", "-f", compose, "start")
        atomic_group_json(root / "last-backup.json", metadata, host["gid"])
        return metadata


def restore(
    host: dict[str, Any], repository: Path, password: Path, snapshot: str, destination: Path
) -> None:
    from applications.host import command

    if destination.exists() or destination.is_symlink() or not snapshot.isalnum():
        raise ServiceError("new_restore_destination_required", 409)
    destination.mkdir(mode=0o700)
    restic = str(Path(host["release"]) / "restic")
    command(
        restic, "--no-cache", "--repo", str(repository), "--password-file", str(password), "check"
    )
    command(
        restic,
        "--no-cache",
        "--repo",
        str(repository),
        "--password-file",
        str(password),
        "restore",
        snapshot,
        "--target",
        str(destination),
    )
    recovered = destination / Path(host["root"]).relative_to("/")
    record = json.loads((recovered / "installation.json").read_text())
    if record["kind"] != host["kind"]:
        raise ServiceError("backup_application_mismatch", 409)
    database = Database(recovered / (host["kind"] + ".db"), record["installation_id"])
    install(database, host["kind"])
    invalidate(database)
    for spool in recovered.glob(".webmail-spool"):
        shutil.rmtree(spool)
    atomic_json(
        destination / "RESTORE-REQUIRES-RECONCILIATION.json",
        {
            "installation_id": record["installation_id"],
            "kind": host["kind"],
            "activate_automatically": False,
            "paired_credentials_revoked": True,
            "trash_cleanup_enabled": False,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("backup", "restore"))
    parser.add_argument("host", type=Path)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--snapshot")
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    host = protected_json(args.host)
    if args.operation == "backup":
        print(json.dumps(backup(host)))
    else:
        if not all((args.repository, args.password_file, args.snapshot, args.destination)):
            parser.error("Restore needs repository, password file, snapshot and a new destination")
        restore(host, args.repository, args.password_file, args.snapshot, args.destination)


if __name__ == "__main__":
    main()
