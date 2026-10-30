"""Export independent application state without changing mail storage or incoming routing."""

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from applications.bootstrap import bootstrap
from applications.schema import install
from backend.database import Database
from backend.secrets import SecretKeys
from backend.webmail_schema import invalidate_restored
from host.webmail_retention import observe
from shared.errors import ServiceError
from shared.files import atomic_json
from shared.installation import Site

TABLES = (
    "mailbox_users",
    "mailbox_labels",
    "mailbox_trash",
    "mailbox_sends",
    "mailbox_permanent_deletions",
)


def copy_rows(
    source: sqlite3.Connection, target: sqlite3.Connection, table: str, transform: Any = None
) -> None:
    if table not in TABLES:
        raise ValueError("Unsupported migration table")
    columns = [row[1] for row in source.execute("PRAGMA table_info(" + table + ")")]
    if not columns:
        return
    for row in source.execute("SELECT * FROM " + table):  # noqa: S608 -- closed table allowlist.
        values = dict(zip(columns, row, strict=True))
        if transform is not None:
            values = transform(values)
        target.execute(
            "INSERT INTO "
            + table
            + "("
            + ",".join(columns)
            + ") VALUES("
            + ",".join("?" for _ in columns)
            + ")",
            tuple(values[column] for column in columns),
        )


def adopt(
    source: Database,
    old_keys: SecretKeys,
    store: Path,
    config: Site,
    management: Path,
    webmail: Path,
    webmail_hostname: str,
    resources: dict[str, int | float],
) -> dict[str, Any]:
    if any(path.exists() or path.is_symlink() for path in (management, webmail)):
        raise ServiceError("new_migration_destinations_required", 409)
    # The reviewed migration configuration is authoritative before any export
    # exists; a fresh host has no legacy /etc/mail-admin/site.json to consult.
    previous = os.environ.get("MAIL_CONFIG")
    with tempfile.TemporaryDirectory(prefix="mail-adoption-") as directory:
        path = Path(directory) / "site.json"
        atomic_json(path, config.public())
        os.environ["MAIL_CONFIG"] = str(path)
        try:
            observed = observe(store / "imapsql.db")
        finally:
            if previous is None:
                os.environ.pop("MAIL_CONFIG", None)
            else:
                os.environ["MAIL_CONFIG"] = previous
    management.mkdir(mode=0o700)
    webmail.mkdir(mode=0o700)
    bootstrap(management, "management", config.hostname, resources)
    bootstrap(webmail, "webmail", webmail_hostname, resources)
    os.environ["MAIL_CONFIG"] = str(management / "site.json")
    # Consistent SQLite snapshots only; never copy live database files or mailbox contents.
    snapshot = management / "source.snapshot.db"
    source.snapshot(snapshot)
    (management / "management.db").unlink()
    snapshot.rename(management / "management.db")
    record = json.loads((management / "installation.json").read_text())
    record["installation_id"] = source.identity
    atomic_json(management / "installation.json", record)
    for filename, key in (
        ("encryption.key", old_keys.encryption),
        ("verifier.key", old_keys.verifier),
    ):
        (management / "secrets" / filename).write_bytes(key)
    target = Database(management / "management.db", source.identity)
    from backend.webmail_schema import install as install_mail_schema

    install_mail_schema(target)
    install(target, "management")
    web_record = json.loads((webmail / "installation.json").read_text())
    web_database = Database(webmail / "webmail.db", web_record["installation_id"])
    new_keys = SecretKeys(
        (webmail / "secrets/encryption.key").read_bytes(),
        (webmail / "secrets/verifier.key").read_bytes(),
    )
    preserved = []

    def profile(values: dict[str, Any]) -> dict[str, Any]:
        native = observed.get(values["email"])
        if native is None or native["inbox_validity"] != values["inbox_validity"]:
            values["active"] = 0
        if values["active"] and native is not None:
            preserved.append(
                (
                    values["id"],
                    values["email"],
                    native["native_id"],
                    native["inbox_validity"],
                    values["created"],
                )
            )
        if values["totp_secret"]:
            secret = old_keys.open(values["totp_secret"], "mailbox-totp:" + values["id"])
            values["totp_secret"] = new_keys.seal(secret, "mailbox-totp:" + values["id"])
        values.update(pending_secret=None, pending_expiry=0, pending_session=None)
        return values

    with target.transaction() as source_connection, web_database.transaction() as destination:
        for table in TABLES:
            copy_rows(
                source_connection, destination, table, profile if table == "mailbox_users" else None
            )
        invalidate_restored(destination)
        destination.execute("UPDATE application_state SET configured=1")
    with target.transaction() as connection:
        connection.executemany(
            "INSERT INTO managed_mailbox_identities(id,email,native_id,inbox_validity,created) "
            "VALUES(?,?,?,?,?)",
            preserved,
        )
        # Management keeps mailbox identities and account metadata, never private webmail profiles.
        for table in ("mailbox_sessions", "mailbox_recovery_codes", *reversed(TABLES)):
            connection.execute("DELETE FROM " + table)  # noqa: S608 -- fixed migration tables.
        connection.execute("DELETE FROM sessions")
        connection.execute("DELETE FROM auth_challenges")
        connection.execute("DELETE FROM otp_codes")
        connection.execute("UPDATE admin_auth SET revision=revision+1,bootstrap_hash=NULL")
        connection.execute(
            "UPDATE application_state SET configured=1,settings=?",
            (
                json.dumps(
                    {
                        "delivery_mode": "adopted_unchanged",
                        "webmail_enabled": False,
                        "allowlist": [],
                    }
                ),
            ),
        )
    atomic_json(management / "site.json", config.public())
    atomic_json(webmail / "site.json", {**config.public(), "hostname": webmail_hostname})
    # A new Webmail administrator must enroll independently using an offline-issued token.
    result = {
        "mail_store_copied": False,
        "routing_changed": False,
        "sessions_revoked": True,
        "preserved_mailbox_identities": len(preserved),
        "webmail_enabled": False,
        "mailbox_recovery_codes": "Reissue after mailbox password and authenticator login",
        "management_id": source.identity,
        "webmail_id": web_record["installation_id"],
        "activation_required": True,
        "tls_certificates_required": True,
    }
    atomic_json(management / "adoption.json", result)
    atomic_json(webmail / "adoption.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--source-marker", type=Path, required=True)
    parser.add_argument("--source-secrets", type=Path, required=True)
    parser.add_argument("--mail-store", type=Path, required=True)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--management-output", type=Path, required=True)
    parser.add_argument("--webmail-output", type=Path, required=True)
    parser.add_argument("--webmail-hostname", required=True)
    parser.add_argument("--hash-memory-kib", type=int, required=True)
    parser.add_argument("--http-concurrency", type=int, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    from shared.files import protected_json

    marker = protected_json(args.source_marker)
    source = Database(args.source_database, marker["installation_id"])
    keys = SecretKeys.load(args.source_secrets, args.source_marker)
    config = Site.parse(protected_json(args.site))
    print(
        json.dumps(
            adopt(
                source,
                keys,
                args.mail_store,
                config,
                args.management_output,
                args.webmail_output,
                args.webmail_hostname,
                {
                    "hash_memory_kib": args.hash_memory_kib,
                    "hash_parallelism": 1,
                    "http_concurrency": args.http_concurrency,
                    "operation_timeout": 15,
                },
            )
        )
    )


if __name__ == "__main__":
    main()
