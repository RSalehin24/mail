"""Root-only one-off image utility. No HTTP route invokes this module."""

import argparse
import getpass
import os
import sys
from pathlib import Path

import pyotp

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from backend.enrollment import Enrollment
from backend.offline import OfflineRecovery
from backend.provisioning import InstallationPaths, create_installation
from backend.services import Services
from shared.errors import ServiceError
from shared.files import protected_bytes
from shared.runtime_policy import RUNTIME_POLICY_NAME


def _private_terminal() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ServiceError("private_terminal_required", 403)


def recover(application: Services) -> None:
    _private_terminal()
    recovery = OfflineRecovery(application.authentication)
    recovery.begin()
    password = getpass.getpass("New master password: ")
    if password != getpass.getpass("Confirm master password: "):
        raise ServiceError("password_confirmation_mismatch", 400)
    secret = pyotp.random_base32()
    print("Enroll this replacement TOTP secret in your authenticator:", secret)
    print("Use a step newer than the last accepted code. Interrupted recovery remains closed.")
    code = getpass.getpass("New authenticator code: ")
    codes = recovery.complete(password, (secret, code))
    print("Save these five single-use recovery codes now:")
    for value in codes:
        print(value)


def provision(arguments: argparse.Namespace) -> None:
    paths = InstallationPaths(
        Path("/app/data/admin_data.db"),
        Path("/run/operator/secrets"),
        Path("/run/operator/installation.json"),
    )
    create_installation(paths)
    os.chown(paths.database, arguments.uid, arguments.gid)
    for path in (
        paths.marker,
        paths.secrets / "encryption.key",
        paths.secrets / "verifier.key",
        paths.secrets / RUNTIME_POLICY_NAME,
    ):
        os.chown(path, 0, arguments.gid)
        os.chmod(path, 0o640)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "provision",
            "bootstrap",
            "recover",
            "snapshot",
            "probe",
            "restore-prepare",
            "encrypt",
            "decrypt",
            "extract",
            "webmail-migrate",
        ),
    )
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    arguments = parser.parse_args()
    if os.geteuid() != 0:
        raise ServiceError("root_required", 403)
    if arguments.action in {"encrypt", "decrypt", "extract"}:
        _archive(arguments.action)
        return
    if arguments.action == "provision":
        if arguments.uid is None or arguments.gid is None or min(arguments.uid, arguments.gid) <= 0:
            raise ServiceError("service_identity_required", 400)
        provision(arguments)
        return
    application = Services.operator()
    try:
        _authentication_action(arguments.action, application)
    finally:
        application.close()
        if arguments.uid is not None and arguments.gid is not None:
            for suffix in ("", "-journal", "-wal", "-shm"):
                path = Path("/app/data/admin_data.db" + suffix)
                if path.exists():
                    os.chown(path, arguments.uid, arguments.gid)
                    os.chmod(path, 0o600)


def _authentication_action(action: str, application: Services) -> None:
    if action == 'webmail-migrate':
        from backend.webmail_schema import install
        install(application.authentication.database)
    elif action == "bootstrap":
        _private_terminal()
        print(Enrollment(application.authentication).issue_bootstrap())
    elif action == "recover":
        recover(application)
    elif action == "snapshot":
        application.authentication.database.snapshot(Path("/snapshot/authentication.db"))
    elif action == "restore-prepare":
        OfflineRecovery(application.authentication).prepare_restore()
    else:
        with application.authentication.database.transaction() as connection:
            row = connection.execute(
                "SELECT initialized,recovery_required FROM admin_auth WHERE id=1"
            ).fetchone()
            if not row[0] or row[1]:
                raise ServiceError("recovery_incomplete", 503)


def _archive(action: str) -> None:
    if action == "extract":
        extract_archive(Path("/archive/restored.tar"), Path("/archive/restored"))
        return
    key = protected_bytes(Path("/archive-key"))
    if action == "encrypt":
        encrypt_archive(Path("/archive/plaintext.tar"), Path("/archive/sealed.bin"), key)
    else:
        decrypt_archive(Path("/archive/sealed.bin"), Path("/archive/restored.tar"), key)


if __name__ == "__main__":
    main()
