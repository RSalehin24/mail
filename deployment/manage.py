"""Explicit local recovery operations; all ordinary configuration uses the authenticated UI."""

import argparse
import json
import os
import uuid
from pathlib import Path

from applications.client import ManagementClient
from applications.runtime import load
from backend.enrollment import Enrollment
from shared.errors import ServiceError
from shared.files import atomic_group_json, protected_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "setup-token",
            "qualify",
            "verify-pilot",
            "reconcile-trash",
            "prepare-qualification",
            "reconcile-account",
        ),
    )
    parser.add_argument("host", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mailbox")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--retain-owner", action="store_true")
    parser.add_argument("--new-incarnation", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    host = protected_json(args.host)
    root = Path(host["root"])
    # Operator commands must never initialize the serving process's credential
    # vault or attachment spool, or invalidate its live mailbox sessions.
    context = load(root, host["kind"], mailbox_runtime=False)
    remote = ManagementClient(context.authentication.database, context.authentication.keys)
    try:
        database = context.authentication.database
        if args.operation == "setup-token":
            print(Enrollment(context.authentication).issue_bootstrap())
        elif args.operation == "qualify":
            from applications.qualification import validate

            if args.report is None or host["kind"] != "webmail":
                parser.error("qualify needs a report and the Webmail installation")
            report = json.loads(args.report.read_text())
            validate(report)
            paired = remote.status()
            if report["hosts"]["management"]["installation_id"] != paired["management_id"]:
                raise ServiceError("qualification_installation_mismatch", 409)
            if report["hosts"]["webmail"]["containers"]["mail-webmail"]["image"] != host["image"]:
                raise ServiceError("qualification_image_mismatch", 409)
            if report["hosts"]["webmail"]["installation_id"] != database.identity:
                raise ServiceError("qualification_installation_mismatch", 409)
            atomic_group_json(root / "qualification.json", report, host["gid"])
        elif args.operation == "verify-pilot":
            if host["kind"] != "webmail" or not args.mailbox:
                parser.error("verify-pilot needs a Webmail installation and a mailbox")
            remote.identify(args.mailbox)
            with database.transaction() as connection:
                values = json.loads(
                    connection.execute("SELECT settings FROM application_state").fetchone()[0]
                )
                if values.get("allowlist") != [args.mailbox] or not values.get("webmail_enabled"):
                    raise ServiceError("enable_and_verify_one_pilot_first", 409)
                values["pilot_verified"] = True
                connection.execute("UPDATE application_state SET settings=?", (json.dumps(values),))
        elif args.operation == "reconcile-account":
            if (
                host["kind"] != "management"
                or not args.mailbox
                or args.retain_owner == args.new_incarnation
            ):
                parser.error("Choose a mailbox and exactly one verified ownership decision")
            from applications.host import deployment
            from helper.maddy import DockerMaddy
            from shared.validation import address

            email = address(args.mailbox)
            native_account = DockerMaddy(deployment(host)).identity(email)
            with database.transaction() as connection:
                row = connection.execute(
                    "SELECT * FROM managed_mailbox_identities WHERE email=? AND state!='retired'",
                    (email,),
                ).fetchone()
                if row is None:
                    raise ServiceError("account_not_found", 404)
                if args.retain_owner:
                    if row["native_id"] != native_account["native_id"]:
                        raise ServiceError("new_account_incarnation_requires_new_identity", 409)
                    connection.execute(
                        "UPDATE managed_mailbox_identities SET state='active',revision=revision+1,"
                        "inbox_validity=?,credential_fingerprint=? WHERE id=?",
                        (
                            native_account["inbox_validity"],
                            native_account["credential_fingerprint"],
                            row["id"],
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE managed_mailbox_identities SET state='retired',revision=revision+1 "
                        "WHERE id=?",
                        (row["id"],),
                    )
                connection.execute("UPDATE application_state SET cleanup_enabled=0")
            print("Ownership decision recorded; reconcile Trash references before cleanup.")
        elif args.operation == "prepare-qualification":
            if not host.get("qualification_only") or args.fixture is None:
                raise ServiceError("isolated_qualification_installation_required", 403)
            fixture = protected_json(args.fixture)
            accounts = fixture.get("accounts", [])
            if (
                fixture.get("scope") != "isolated-two-host-mail-qualification"
                or len(accounts) != 10
                or not all(email.startswith("qualify-") for email in accounts)
            ):
                raise ServiceError("ten_synthetic_qualification_mailboxes_required", 400)
            if host["kind"] == "management":
                for email in accounts:
                    context.mailboxes.create(
                        {
                            "request_id": str(uuid.uuid4()),
                            "email": email,
                            "password": fixture["password"],
                        }
                    )
            else:
                for email in accounts:
                    remote.identify(email)
                with database.transaction() as connection:
                    values = json.loads(
                        connection.execute("SELECT settings FROM application_state").fetchone()[0]
                    )
                    values.update(webmail_enabled=True, allowlist=accounts)
                    connection.execute(
                        "UPDATE application_state SET settings=?", (json.dumps(values),)
                    )
                (root / "restart-requested").touch(mode=0o600)
            print("Synthetic qualification mailboxes prepared; production rollout remains gated.")
        else:
            if host["kind"] != "management":
                parser.error("Trash reconciliation runs on Mail Management")
            from applications.host import command, deployment
            from host.locks import HostLock, LockPolicy
            from host.webmail_retention import observe

            # Stop the paired Webmail before this operation, after flushing its outbox.
            with HostLock(Path("/run/lock/mail-management.lock"), LockPolicy(2)):
                active = command(
                    "/usr/bin/docker", "inspect", "--format", "{{.State.Running}}", "maddy"
                )
                if active.strip() != "true":
                    raise ServiceError("maddy_unavailable", 503)
                observed = observe(Path(deployment(host)["maddy_data_directory"]) / "imapsql.db")
                with database.transaction() as connection:
                    if connection.execute(
                        "SELECT 1 FROM trash_peers WHERE seen>?",
                        (__import__("time").time() - 120,),
                    ).fetchone():
                        raise ServiceError("stop_and_flush_webmail_before_reconciliation", 409)
                    for row in connection.execute(
                        "SELECT * FROM managed_mailbox_identities WHERE state='active'"
                    ):
                        native = observed.get(row["email"])
                        if (
                            native is None
                            or native["native_id"] != row["native_id"]
                            or native["inbox_validity"] != row["inbox_validity"]
                        ):
                            raise ServiceError("trash_identity_reconciliation_required", 409)
                    connection.execute("DELETE FROM managed_trash")
                    connection.execute("DELETE FROM trash_holds")
                    connection.execute("UPDATE application_state SET cleanup_enabled=1")
            print("References verified; Trash ages restart on the next native observation.")
    finally:
        remote.close()
        context.close()


if __name__ == "__main__":
    main()
