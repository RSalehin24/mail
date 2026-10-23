"""Synthetic recovery fixtures; the peer directory is not an off-VM production backup."""

import hashlib
import imaplib
import json
import os
import secrets
import shutil
import smtplib
import ssl
import time
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path

import pyotp
from argon2 import PasswordHasher

from backend.archive import decrypt_archive, encrypt_archive, extract_archive
from backend.auth import AuthDependencies, Authentication, Credentials, Verification
from backend.database import Database
from backend.enrollment import Enrollment
from backend.offline import OfflineRecovery
from backend.provisioning import InstallationPaths, create_installation
from backend.relay import Relay, RelayDependencies
from backend.secrets import SecretKeys
from helper.maddy import LAUNCHER
from host.backup import Backup, BackupDependencies, BackupPaths
from host.locks import HostLock
from shared.files import protected_json
from tests.api_support import FakeRelayTransport
from tests.auth_support import PASSWORD
from tests.maddy_support import IMAGE
from tools.resources import MADDY_RUNTIME_TMPFS_BYTES, RESTORE_MEMORY_MIB


def deliver(fixture, email, body):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = (
        "fixture@example.net",
        email,
        "Isolated recovery fixture",
    )
    message.set_content(body)
    with smtplib.SMTP("127.0.0.1", fixture.smtp_port, timeout=5) as smtp:
        if smtp.send_message(message):
            raise RuntimeError("Synthetic delivery rejected")


def bodies(connection):
    if connection.select("INBOX")[0] != "OK":
        raise RuntimeError("Fixture inbox unavailable")
    status, identifiers = connection.search(None, "ALL")
    if status != "OK" or not identifiers[0]:
        raise RuntimeError("Fixture message missing")
    result = []
    for identifier in identifiers[0].split():
        status, content = connection.fetch(identifier, "(RFC822)")
        if status != "OK" or not isinstance(content[0], tuple):
            raise RuntimeError("Fixture message body unavailable")
        result.append(content[0][1])
    return b"\n".join(result)


class FixtureWriters:
    def __init__(self, fixture):
        self.fixture = fixture
        self.journal = fixture.helper.journal
        self.lock = fixture.helper.dependencies
        self.events = []
        self.renewal = fixture.root / "renewal.lock"
        self.renewal.touch(mode=0o600)

    @contextmanager
    def consistent(self):
        with (
            HostLock(self.renewal, self.lock.lock_policy),
            HostLock(self.lock.lock, self.lock.lock_policy),
        ):
            if self.journal.unresolved():
                raise RuntimeError("Unresolved fixture operation")
            self.events.append("locked")
            yield
        self.events.append("unlocked")

    def inventories(self):
        return self.fixture.maddy.inventories()

    def running(self):
        return ["maddy"]

    def pause(self, names):
        if names != ["maddy"]:
            raise RuntimeError("Unexpected writers")
        self.fixture.command(("stop", self.fixture.identifier))
        self.events.append("stopped")

    def resume(self, names):
        for name in names:
            self.fixture.command(("start", name))
        self.fixture.refresh_ports()
        self.fixture._wait()
        self.events.append("resumed")


class FixtureCipher:
    def __init__(self):
        self.key = secrets.token_bytes(32)

    def encrypt(self, source, destination):
        encrypt_archive(source, destination, self.key)


class FixturePeer:
    def __init__(self, path):
        self.path = path
        path.mkdir(mode=0o700)

    def publish(self, source):
        target = self.path / source.name
        shutil.copy2(source, target)
        return str(target)

    def fetch(self, reference, destination):
        shutil.copy2(reference, destination)


class DashboardRecoveryFixture:
    def __init__(self, directory):
        self.data, self.operator = directory / "dashboard", directory / "operator"
        self.data.mkdir(mode=0o700)
        self.operator.mkdir(mode=0o700)
        keys = self.operator / "secrets"
        keys.mkdir(mode=0o700)
        self.paths = InstallationPaths(
            self.data / "admin_data.db", keys, self.operator / "installation.json"
        )
        self.database, self.keys = create_installation(self.paths)
        self.now = time.time() - 120
        self.hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
        self.auth = Authentication(
            AuthDependencies(self.database, self.keys, self.hasher, lambda: self.now, True)
        )
        self._enroll()
        self._runtime_permissions()

    def _runtime_permissions(self):
        os.chown(self.data, 10001, 10001)
        self.paths.secrets.chmod(0o750)
        os.chown(self.paths.secrets, 0, 10001)
        for path in (self.paths.marker, *self.paths.secrets.iterdir()):
            os.chown(path, 0, 10001)
            path.chmod(0o640)

    def _enroll(self):
        enrollment = Enrollment(self.auth)
        token = enrollment.issue_bootstrap()
        self.seed = enrollment.begin(token, "restore-fixture")["secret"]
        self.codes = enrollment.confirm(
            {"token": token, "password": PASSWORD, "code": pyotp.TOTP(self.seed).at(self.now)},
            "restore-fixture",
        )
        self.now += 30
        challenge = self.auth.password_challenge(
            Credentials("rsalehin24@gmail.com", PASSWORD, "restore-fixture")
        )
        self.session = self.auth.verify(
            Verification(challenge, "recovery", self.codes[0], "restore-fixture"), "login"
        )
        self.challenge = self.auth.password_challenge(
            Credentials("rsalehin24@gmail.com", PASSWORD, "restore-fixture")
        )
        relay = Relay(
            RelayDependencies(self.database, self.keys, FakeRelayTransport(), lambda: self.now)
        )
        relay.save(
            {
                "host": "smtp-relay.brevo.com",
                "port": 587,
                "tls_mode": "starttls",
                "username": "synthetic",
                "credential": "fixture-encrypted-relay-secret",
            }
        )
        test = relay.test("mail@rsalehin24.me", ("delivery", "restore-fixture"))
        relay.confirm(test["test_id"])
        os.chown(self.paths.database, 10001, 10001)

    def recover(self, snapshot):
        authentication, relay = self.load(snapshot)
        recovery = OfflineRecovery(authentication)
        recovery.begin()
        self.now += 60
        seed = pyotp.random_base32()
        self.replacement_seed = seed
        recovery.complete(PASSWORD, (seed, pyotp.TOTP(seed).at(self.now)))
        return authentication, relay

    def load(self, snapshot):
        operator = snapshot / "operator"
        marker = protected_json(operator / "installation.json")
        keys = SecretKeys.load(operator / "secrets", operator / "installation.json")
        database = Database(snapshot / "dashboard/admin_data.db", marker["installation_id"])
        authentication = Authentication(
            AuthDependencies(database, keys, self.hasher, lambda: self.now, True)
        )
        return authentication, Relay(
            RelayDependencies(database, keys, FakeRelayTransport(), lambda: self.now)
        )


class RecoveryArchive:
    def __init__(self, fixture, dashboard, body):
        self.fixture, self.dashboard = fixture, dashboard
        self.writers, self.cipher = FixtureWriters(fixture), FixtureCipher()
        self.body = next(
            path
            for path in fixture.data.rglob("*")
            if path.is_file()
            and not path.name.startswith(("credentials.db", "imapsql.db"))
            and body in path.read_bytes()
        )
        directory = fixture.root / "staging"
        directory.mkdir(mode=0o700)
        root = Path(__file__).resolve().parents[1]
        sources = {
            "maddy": fixture.data,
            "dashboard": dashboard.data,
            "operator": dashboard.operator,
            "journal": fixture.helper.journal.path,
            "launcher": root / "host/maddy-launcher",
            "units": root / "host/systemd",
            "configuration": fixture.configuration,
            "tls": fixture.tls,
        }
        paths = BackupPaths(
            directory,
            fixture.root / "manifest.json",
            sources,
            (str(self.body.relative_to(fixture.data).parent),),
        )
        self.backup = Backup(
            paths,
            BackupDependencies(
                self.writers,
                self.writers,
                self.cipher,
                FixturePeer(fixture.root / "peer"),
                time.time,
            ),
        )

    def capture(self):
        self.snapshot = self.backup.capture()
        self.manifest = self.backup.transfer(self.snapshot)
        sealed = Path(self.manifest["reference"])
        raw, destination = self.fixture.root / "restore.tar", self.fixture.root / "restored"
        decrypt_archive(sealed, raw, self.cipher.key)
        extract_archive(raw, destination)
        self.restored = destination / "snapshot"
        return self.restored

    @contextmanager
    def maddy(self):
        name = "mail-admin-restore-" + self.snapshot.name
        command = (
            "create",
            "--name",
            name,
            "--label",
            "mail-admin.fixture=true",
            "--memory",
            str(RESTORE_MEMORY_MIB) + "m",
            "--memory-swap",
            str(RESTORE_MEMORY_MIB) + "m",
            "--cpus",
            "0.5",
            "--pids-limit",
            "128",
            "--tmpfs",
            f"/run/maddy:size={MADDY_RUNTIME_TMPFS_BYTES},mode=0700",
            "-p",
            "127.0.0.1::993",
        )
        for source, target in (
            ("maddy", "/data"),
            ("configuration", "/data/maddy.conf"),
            ("tls", "/data/tls"),
            ("launcher", "/usr/local/libexec/mail-admin-maddy"),
        ):
            readonly = "" if source == "maddy" else ",readonly"
            command += (
                "--mount",
                "type=bind,src="
                + self.fixture.daemon_path(self.restored / source)
                + f",dst={target}{readonly}",
            )
        command += (
            "--entrypoint",
            "/usr/local/libexec/mail-admin-maddy",
            IMAGE,
            "-config",
            "/data/maddy.conf",
            "run",
        )
        identifier = self.fixture.command(command).stdout.strip()
        self.writers.pause(["maddy"])
        try:
            self.fixture.command(("start", identifier))
            state = json.loads(self.fixture.command(("inspect", identifier)).stdout)[0]
            port = int(state["NetworkSettings"]["Ports"]["993/tcp"][0]["HostPort"])
            with self._connect(port) as connection:
                yield connection
        finally:
            self.fixture.command(("rm", "--force", identifier))
            self.writers.resume(["maddy"])

    def _connect(self, port):
        context = ssl.create_default_context(cafile=self.fixture.certificates.trust)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                return imaplib.IMAP4_SSL("127.0.0.1", port, ssl_context=context, timeout=2)
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Restored Maddy unavailable")

    def repair_body(self):
        relative = self.body.relative_to(self.fixture.data)
        source = self.restored / "maddy" / relative
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        self.writers.pause(["maddy"])
        try:
            if self.body.exists():
                raise RuntimeError("Repair refuses to overwrite an existing body")
            shutil.copy2(source, self.body)
            os.chown(self.body, source.stat().st_uid, source.stat().st_gid)
        finally:
            self.writers.resume(["maddy"])
        return expected, hashlib.sha256(self.body.read_bytes()).hexdigest()

    def restore_reference(self, email):
        relative = self.body.relative_to(self.fixture.data)
        source = self.restored / "maddy" / relative
        result = self.fixture.command(
            ("exec", "-i", "maddy", LAUNCHER, "imap-msgs", "add", email, "INBOX"),
            source.read_text(),
        )
        identifier = result.stdout.strip()
        if not identifier.isdigit():
            raise RuntimeError("Recovery did not return a new native message reference")
        return identifier
