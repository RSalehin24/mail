"""Local privileged helper and bounded host maintenance. Never installed on the Webmail peer."""

import argparse
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from applications import VERSION
from applications.identity import Identities
from applications.trash import TrashAuthority
from backend.database import Database
from helper.journal import Journal
from helper.maddy import DockerMaddy, cli_succeeded
from helper.process import Command, execute
from helper.server import HelperServer, SocketPolicy
from helper.service import HelperDependencies, HostHelper
from helper.status import host_status
from host.locks import HostLock, LockPolicy
from host.webmail_retention import TrashRetention, observe
from shared.errors import ServiceError
from shared.files import protected_json
from shared.installation import site
from shared.submission import relay_configuration


def command(*args: str) -> str:
    result = execute(Command(tuple(args), timeout=30))
    if result.code:
        raise ServiceError("host_command_failed", 503)
    return result.output


def provision_lock(host: dict[str, Any]) -> Path:
    path = Path(
        "/run/lock/mail-admin-maddy.lock"
        if host.get("adopted")
        else "/run/lock/mail-" + host["kind"] + ".lock"
    )
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR, 0o600)
    except FileExistsError:
        # HostLock validates ownership, mode and file type before using it.
        pass
    else:
        os.close(descriptor)
    return path


def deployment(host: dict[str, Any]) -> dict[str, Any]:
    if host.get("adopted"):
        return protected_json(Path(host["legacy_deployment"]))
    data = Path("/var/lib/mail-management-store")
    return {
        "inputs": {"maddy_image_id": host["maddy_image"], "operation_timeout_seconds": 15},
        "maddy_cli_version": "0.9.6",
        "maddy_data_directory": str(data),
        "maddy_mounts": {
            "/data": str(data),
            "/usr/local/libexec/mail-admin-maddy": str(
                host.get("maddy_launcher_path", Path(host["release"]) / "host/maddy-launcher")
            ),
        },
    }


class DeletePolicy:
    def __init__(self, root: Path, configuration: dict[str, Any]) -> None:
        self.root, self.configuration = root, configuration

    def capability(self) -> dict[str, Any]:
        if not (self.root / "last-backup.json").is_file():
            raise ServiceError("verified_backup_required", 409)
        return {"enabled": True}

    def check(self, request: Any, journal: Journal) -> None:
        self.capability()
        backup = protected_json(self.root / "last-backup.json")
        if time.time() - backup["created"] > 86400 or request.cutoff != backup["id"]:
            raise ServiceError("recent_verified_backup_required", 409)
        from shared.validation import RESERVED

        if request.email.split("@", 1)[0] in RESERVED or request.email == site().otp_sender:
            raise ServiceError("protected_recipient", 403)
        native = DockerMaddy(self.configuration).identity(request.email)
        if backup.get("accounts", {}).get(request.email) != native:
            raise ServiceError("backup_account_coverage_mismatch", 409)


def serve(host: dict[str, Any]) -> None:
    provision_lock(host)
    root = Path(host["root"])
    os.environ["MAIL_CONFIG"] = str(root / "site.json")
    data = Path("/var/lib/mail-management-helper")
    data.mkdir(mode=0o700, exist_ok=True)
    journal = (
        Journal(data / "journal.db")
        if (data / "journal.db").exists()
        else Journal.provision(data / "journal.db")
    )
    dependencies = HelperDependencies(
        DockerMaddy(deployment(host)),
        journal,
        DeletePolicy(root, deployment(host)),
        Path(
            "/run/lock/mail-admin-maddy.lock"
            if host.get("adopted")
            else "/run/lock/mail-management.lock"
        ),
        LockPolicy(2),
        time.time,
        host_status,
    )
    with HelperServer(
        Path("/run/mail-management-helper/control.sock"),
        # Accommodate the integration listener's twelve bounded requests plus
        # browser administration without turning bursts into rejected identity checks.
        (HostHelper(dependencies), SocketPolicy(host["uid"], host["gid"], 15, concurrency=16)),
    ) as server:
        server.serve_forever(poll_interval=0.5)


def render_maddy() -> str:
    from shared.submission import submission_configuration

    config = site()
    return f"""hostname {config.mail_hostname}
state_dir /data
runtime_dir /run/maddy
tls file /data/tls/fullchain.pem /data/tls/privkey.pem
auth.pass_table local_authdb {{
 table sql_table {{
  driver sqlite3
  dsn credentials.db
  table_name passwords
 }}
}}
storage.imapsql local_mailboxes {{
 driver sqlite3
 dsn imapsql.db
}}
smtp tcp://0.0.0.0:25 {{
 limits {{
  all concurrency 10
  all rate 20 1s
 }}
 default_source {{
  destination {config.domain} {{
   deliver_to &local_mailboxes
  }}
  default_destination {{
   reject 550 5.1.1 "Local recipients only"
  }}
 }}
}}
imap tls://0.0.0.0:993 {{
 auth &local_authdb
 storage &local_mailboxes
}}
import /data/submission-relay.conf
""" + submission_configuration()


def reconcile(host: dict[str, Any]) -> None:
    provision_lock(host)
    root = Path(host["root"])
    os.environ["MAIL_CONFIG"] = str(root / "site.json")
    record = json.loads((root / "installation.json").read_text())
    database = Database(root / (host["kind"] + ".db"), record["installation_id"], busy_timeout=1)
    with database.transaction() as connection:
        ready = connection.execute(
            "SELECT configured AND (SELECT initialized FROM admin_auth) FROM application_state"
        ).fetchone()[0]
    if host["kind"] == "management" and ready:
        from datetime import UTC, datetime, timedelta

        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        from applications.certificates import csr, issue
        from helper.submission import write_configuration

        certificate = x509.load_pem_x509_certificate((root / "integration.pem").read_bytes())
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value.get_values_for_type(x509.DNSName)
        if site().hostname not in names or certificate.not_valid_after_utc <= datetime.now(
            UTC
        ) + timedelta(days=30):
            from cryptography.hazmat.primitives.asymmetric import ec

            key = serialization.load_pem_private_key(
                (root / "secrets/integration.key").read_bytes(), password=None
            )
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise ServiceError("unsupported_integration_server_key", 503)
            pem, _ = issue(
                (root / "secrets/integration-ca.key").read_text(),
                (root / "integration-ca.pem").read_text(),
                csr(key, site().hostname),
                site().hostname,
                365 * 86400,
                server=True,
            )
            write_configuration(root / "integration.pem", pem.encode())
            os.chown(root / "integration.pem", host["uid"], host["gid"])
            (root / "restart-requested").touch(mode=0o600)
    if host["kind"] == "management" and ready and not host.get("adopted"):
        from backend.secrets import SecretKeys

        keys = SecretKeys(
            (root / "secrets/encryption.key").read_bytes(),
            (root / "secrets/verifier.key").read_bytes(),
        )
        with database.transaction() as connection:
            relay = connection.execute("SELECT * FROM relay_settings").fetchone()
        if relay is None:
            raise ServiceError("relay_configuration_required", 409)
        data = Path("/var/lib/mail-management-store")
        data.mkdir(mode=0o700, exist_ok=True)
        (data / "tls").mkdir(mode=0o700, exist_ok=True)
        desired = {
            "maddy.conf": render_maddy(),
            "submission-relay.conf": relay_configuration(
                {
                    "username": relay["username"],
                    "credential": keys.open(relay["credential"], "relay-credential"),
                }
            ),
        }
        changed = False
        for name, content in desired.items():
            target = data / name
            if not target.exists() or target.read_text() != content:
                from helper.submission import write_configuration

                write_configuration(target, content.encode())
                changed = True
        shutil.copyfile(
            Path(host["release"]) / "host/submission-from.awk", data / "submission-from.awk"
        )
        for name in ("fullchain.pem", "privkey.pem"):
            target = data / "tls" / name
            tls_bytes = (root / "tls/active" / name).read_bytes()
            if not target.exists() or target.read_bytes() != tls_bytes:
                from helper.submission import write_configuration

                write_configuration(target, tls_bytes)
                changed = True
        identity = command("/usr/bin/docker", "ps", "-aq", "--filter", "name=^/maddy$").strip()
        if not identity:
            command(
                "/usr/bin/docker",
                "run",
                "-d",
                "--name",
                "maddy",
                "--restart",
                "unless-stopped",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                str(host["maddy_memory_mib"]) + "m",
                "--pids-limit",
                "100",
                "--tmpfs",
                "/run/maddy:size=1048576,mode=0700",
                "--publish",
                "25:25",
                "--publish",
                "587:587",
                "--publish",
                "993:993",
                "--volume",
                str(data) + ":/data",
                "--volume",
                deployment(host)["maddy_mounts"]["/usr/local/libexec/mail-admin-maddy"]
                + ":/usr/local/libexec/mail-admin-maddy:ro",
                *(
                    ["--env", "GOMEMLIMIT=" + str(host["maddy_go_memory_mib"]) + "MiB"]
                    if host.get("maddy_go_memory_mib")
                    else []
                ),
                host["maddy_image"],
            )
        elif changed:
            maddy = DockerMaddy(deployment(host))
            maddy.validate()
            if not cli_succeeded(maddy.change(("verify-config",))):
                raise ServiceError("maddy_configuration_invalid", 503)
            command("/usr/bin/docker", "restart", "maddy")
    pending = root / "restart-requested"
    if pending.exists():
        pending.unlink()
        command(
            "/usr/bin/docker",
            "compose",
            "-f",
            "/etc/mail-" + host["kind"] + "/compose.json",
            "restart",
        )
    if ready and host["kind"] == "management":
        retention(host, database)


def retention(host: dict[str, Any], database: Database) -> None:
    configuration = deployment(host)
    store = Path(configuration["maddy_data_directory"]) / "imapsql.db"
    native = DockerMaddy(configuration)
    from backend.helper_client import HelperClient

    authority = TrashAuthority(
        database,
        Identities(database, HelperClient(Path("/run/mail-management-helper/control.sock"), 3)),
    )
    with HostLock(
        Path(
            "/run/lock/mail-admin-maddy.lock"
            if host.get("adopted")
            else "/run/lock/mail-management.lock"
        ),
        LockPolicy(2),
    ):
        native.validate()
        due = authority.due(observe(store), time.time())
        if not due:
            return
        # Quiesce all native writers before checking generations and deleting exact UIDs.
        command("/usr/bin/docker", "stop", "--time", "10", "maddy")
        try:
            for item in due:
                current = observe(store)
                TrashRetention.validate_removal(item, current)
                with database.transaction() as connection:
                    held = connection.execute(
                        "SELECT 1 FROM trash_holds WHERE mailbox_id=?",
                        (item["mailbox_id"],),
                    ).fetchone()
                    enabled = connection.execute(
                        "SELECT cleanup_enabled FROM application_state"
                    ).fetchone()[0]
                    active = connection.execute(
                        "SELECT 1 FROM managed_mailbox_identities WHERE id=? AND state='active'",
                        (item["mailbox_id"],),
                    ).fetchone()
                    stale = connection.execute(
                        "SELECT 1 FROM integration_credentials c LEFT JOIN trash_peers p "
                        "ON p.installation_id=c.installation_id WHERE c.revoked=0 AND "
                        "c.activated=1 AND c.expires>? AND (p.seen IS NULL OR p.seen<=?)",
                        (time.time(), time.time() - 120),
                    ).fetchone()
                    if held or not enabled or not active or stale:
                        continue
                    command(
                        "/usr/bin/docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--memory",
                        str(host["maddy_memory_mib"]) + "m",
                        "--volumes-from",
                        "maddy",
                        "--tmpfs",
                        "/run/maddy:size=1048576,mode=0700",
                        "--entrypoint",
                        "/usr/local/libexec/mail-admin-maddy",
                        host["maddy_image"],
                        "imap-msgs",
                        "remove",
                        "--uid",
                        "--yes",
                        item["email"],
                        item["folder"],
                        str(item["uid"]),
                    )
                    if (item["folder"], item["uidvalidity"], item["uid"]) in observe(store).get(
                        item["email"], {}
                    ).get("entries", set()):
                        raise ServiceError("trash_native_removal_unverified", 503)
        finally:
            command("/usr/bin/docker", "start", "maddy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("serve", "reconcile"))
    parser.add_argument("host", type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    host = protected_json(args.host)
    if host["version"] != VERSION:
        raise ServiceError("host_package_version_mismatch", 503)
    (serve if args.operation == "serve" else reconcile)(host)


if __name__ == "__main__":
    main()
