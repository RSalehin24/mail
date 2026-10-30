"""Explicit offline bootstrap used only by the privileged installer."""

import argparse
import json
import os
import secrets
import uuid
from pathlib import Path

from argon2 import PasswordHasher

from applications import VERSION
from applications.certificates import ca, csr, issue, pem_key, private_key
from applications.schema import install
from backend.auth import AuthDependencies, Authentication
from backend.database import Database
from backend.enrollment import Enrollment
from backend.secrets import SecretKeys
from shared.files import atomic_json
from shared.installation import Site, hostname


def bootstrap(root: Path, kind: str, name: str, resources: dict[str, int | float]) -> str:
    hostname(name)
    if any(root.iterdir()):
        raise ValueError("Refusing to overwrite existing installation")
    identity = str(uuid.uuid4())
    for directory in ("secrets", "tls", "tls/bootstrap"):
        (root / directory).mkdir(mode=0o700)
    # Bootstrap placeholders authorize no mail domain or mailbox session.
    config = Site("unconfigured.invalid", name, "admin@unconfigured.invalid", name, "")
    atomic_json(root / "site.json", config.public())
    os.environ["MAIL_CONFIG"] = str(root / "site.json")
    database = Database.provision(root / (kind + ".db"), identity)
    install(database, kind)
    keys = SecretKeys(secrets.token_bytes(32), secrets.token_bytes(32))
    for filename, value in (("encryption.key", keys.encryption), ("verifier.key", keys.verifier)):
        target = root / "secrets" / filename
        target.write_bytes(value)
        target.chmod(0o600)
    auth = Authentication(
        AuthDependencies(
            database,
            keys,
            PasswordHasher(
                time_cost=3,
                memory_cost=int(resources["hash_memory_kib"]),
                parallelism=1,
            ),
            __import__("time").time,
        )
    )
    token = Enrollment(auth).issue_bootstrap()
    atomic_json(
        root / "installation.json",
        {
            "installation_id": identity,
            "kind": kind,
            "version": VERSION,
            "schema_version": 1,
            "resources": resources,
        },
    )
    ca_key, ca_cert = ca(identity)
    key = private_key()
    certificate, fingerprint = issue(ca_key, ca_cert, csr(key, name), name, 86400, server=True)
    (root / "bootstrap-fingerprint.txt").write_text(fingerprint + "\n")
    (root / "tls/bootstrap/fullchain.pem").write_text(certificate)
    (root / "tls/bootstrap/privkey.pem").write_text(pem_key(key))
    (root / "tls/active").symlink_to("bootstrap")
    if kind == "management":
        (root / "secrets/integration-ca.key").write_text(ca_key)
        (root / "integration-ca.pem").write_text(ca_cert)
        key = private_key()
        certificate, _ = issue(ca_key, ca_cert, csr(key, name), name, 365 * 86400, server=True)
        (root / "integration.pem").write_text(certificate)
        (root / "secrets/integration.key").write_text(pem_key(key))
    for target in root.rglob("*"):
        if target.is_file() and not target.is_symlink():
            target.chmod(0o600)
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("management", "webmail"))
    parser.add_argument("root", type=Path)
    parser.add_argument("hostname")
    parser.add_argument("--resources", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    print(bootstrap(args.root, args.kind, args.hostname, json.loads(args.resources)))


if __name__ == "__main__":
    main()
