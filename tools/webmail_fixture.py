"""Private synthetic webmail fixture. Prepare with a local Docker socket; serve without one."""

import argparse
import imaplib
import json
import os
import secrets
import shutil
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def private_json(path: Path, value: dict[str, Any]) -> None:
    with path.open("w") as output:
        os.chmod(path, 0o600)
        json.dump(value, output)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def prepare(directory: Path, count: int) -> None:
    from helper.maddy import DockerMaddy
    from tests.fixture_values import MAILBOX_PASSWORD
    from tests.maddy_support import CONFIGURATION, RealMaddyFixture

    class SecurityMaddy(DockerMaddy):
        def expected_cli_version(self) -> str:
            return "0.9.6"

    class LocalFixture(RealMaddyFixture):
        additional_arguments = ("--network", "host")
        maddy_class = SecurityMaddy

        def image_reference(self) -> str:
            return (
                "ghcr.io/foxcpp/maddy@sha256:"
                "e1074e92a452b84d1135ebe8ab831c5f541d33dcbd39e38fab740a53bbf2f97c"
            )

        def refresh_ports(self, inspected: Any = None) -> None:
            pass

    from helper.protocol import HelperRequest

    directory.mkdir(mode=0o700, parents=True)
    ports = [free_port() for _ in range(4)]
    config = CONFIGURATION.replace("0.0.0.0:25", f"127.0.0.1:{ports[0]}").replace(
        "0.0.0.0:993", f"127.0.0.1:{ports[1]}"
    )
    # Mail can only be delivered to the synthetic local domain. No relay exists.
    config += f"""
submission tcp://127.0.0.1:{ports[2]} {{
 auth &local_authdb
 insecure_auth no
 check {{
  authorize_sender {{
   user_to_email identity
   check_header yes
  }}
 }}
 default_source {{
  destination rsalehin24.me {{ deliver_to &local_mailboxes }}
  default_destination {{ reject 550 5.1.1 "Synthetic local recipients only" }}
 }}
}}
"""
    fixture = LocalFixture(config)
    fixture.smtp_port, fixture.imap_port = ports[:2]
    try:
        fixture.start()
        accounts = [f"webmail-load-{index:02}@rsalehin24.me" for index in range(10)]
        for email in accounts:
            fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )

        def seed_mailbox(email: str) -> None:
            context = ssl.create_default_context(cafile=fixture.certificates.trust)
            with imaplib.IMAP4_SSL(
                "127.0.0.1", fixture.imap_port, ssl_context=context, timeout=60
            ) as imap:
                imap.authenticate(
                    "PLAIN", lambda _: ("\0" + email + "\0" + MAILBOX_PASSWORD).encode()
                )
                imap.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                distinct = min(count, 100)
                for index in range(distinct):
                    payload = (
                        f"From: outside@example.net\r\nTo: {email}\r\n"
                        f"Subject: Synthetic message {index:04}\r\n"
                        f"Message-ID: <{index}-{email}>\r\n"
                        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
                        + "Searchable synthetic mailbox content. "
                        * 40
                    ).encode()
                    if imap.append("INBOX", None, None, payload)[0] != "OK":
                        raise RuntimeError("Synthetic seed append failed")
                remaining = count - distinct
                imap.select("INBOX")
                while remaining:
                    uids = imap.uid("SEARCH", None, "ALL")[1][0].split()
                    chosen = uids[:remaining]
                    if imap.uid("COPY", b",".join(chosen), "INBOX")[0] != "OK":
                        raise RuntimeError("Synthetic seed copy failed")
                    remaining -= len(chosen)
                from email import policy
                from email.message import EmailMessage

                message = EmailMessage(policy=policy.SMTP)
                message["From"] = "outside@example.net"
                message["To"] = email
                message["Subject"] = "Synthetic attachment and HTML"
                message.set_content("Searchable attachment message")
                message.add_alternative(
                    '<p>Hello from webmail.</p><img src="https://tracker.invalid/pixel">'
                    '<script>parent.document.body.innerHTML="attack"</script>',
                    subtype="html",
                )
                message.add_attachment(
                    b"synthetic attachment\n" * 4096,
                    maintype="application",
                    subtype="octet-stream",
                    filename="synthetic.txt",
                )
                if imap.append("INBOX", None, None, message.as_bytes())[0] != "OK":
                    raise RuntimeError("Synthetic attachment seed failed")
            print(f"Seeded {count + 1} messages in {email}", flush=True)

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=3) as seed_pool:
            list(seed_pool.map(seed_mailbox, accounts))
        application = directory / "application"
        application.mkdir(mode=0o700)
        for name in ("fullchain.pem", "privkey.pem"):
            shutil.copyfile(fixture.tls / name, application / name)
        shutil.copyfile(fixture.certificates.trust, application / "ca.pem")
        record = {
            "scope": "isolated-synthetic-webmail",
            "accounts": accounts,
            "password": MAILBOX_PASSWORD,
            "imap_port": ports[1],
            "submission_port": ports[2],
            "web_port": ports[3],
            "installation_id": str(uuid.uuid4()),
            "encryption": secrets.token_hex(32),
            "verifier": secrets.token_hex(32),
            "messages_per_mailbox": count + 1,
            "seed_method": "100 distinct templates per mailbox, expanded by IMAP COPY; "
            "one additional HTML/attachment message; all message UIDs distinct",
        }
        private_json(application / "fixture.json", record)
        # Fixture server runs as the existing non-root runtime UID.
        if os.geteuid() == 0:
            for path in [application, *application.iterdir()]:
                os.chown(path, 10001, 10001)
                os.chmod(path, 0o700 if path.is_dir() else 0o600)
        private_json(
            directory / "control.json",
            {
                "scope": record["scope"],
                "container": fixture.container,
                "container_id": fixture.identifier,
                "maddy_root": str(fixture.root),
                "image": fixture.maddy_image,
                "application": str(application),
                "web_port": ports[3],
            },
        )
        fixture.temporary._finalizer.detach()
    except BaseException:
        fixture.close()
        raise


def serve(directory: Path) -> None:
    import uvicorn
    from argon2 import PasswordHasher

    from backend.auth import AuthDependencies, Authentication, Credentials, Verification
    from backend.config import Settings
    from backend.database import Database
    from backend.enrollment import Enrollment
    from backend.mailboxes import Mailboxes
    from backend.main import create_app
    from backend.relay import Relay, RelayDependencies
    from backend.secrets import SecretKeys
    from backend.services import Services
    from backend.webmail_imap import ImapTransport
    from backend.webmail_sending import Submission
    from backend.webmail_services import MailServices
    from backend.work import WorkPool
    from shared.errors import ServiceError
    from shared.validation import ADMIN_EMAIL

    configuration = json.loads((directory / "fixture.json").read_text())
    if configuration["scope"] != "isolated-synthetic-webmail":
        raise RuntimeError("An isolated fixture record is required")
    keys = SecretKeys(
        bytes.fromhex(configuration["encryption"]), bytes.fromhex(configuration["verifier"])
    )
    database_path = directory / "application.db"
    new = not database_path.exists()
    database = (
        Database.provision(database_path, configuration["installation_id"])
        if new
        else Database(database_path, configuration["installation_id"])
    )
    auth = Authentication(
        AuthDependencies(
            database, keys, PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1), time.time
        )
    )
    if new:
        import pyotp

        enrollment = Enrollment(auth)
        bootstrap = enrollment.issue_bootstrap()
        material = enrollment.begin(bootstrap, "fixture-setup")
        enrollment.confirm(
            {
                "token": bootstrap,
                "password": configuration["password"],
                "code": pyotp.TOTP(material["secret"]).now(),
            },
            "fixture-setup",
        )
    challenge = auth.password_challenge(
        Credentials(ADMIN_EMAIL, configuration["password"], "fixture")
    )
    # Read one unused synthetic recovery verifier only through a newly generated
    # private code. The ordinary administrator login still always requires 2FA.
    from backend.auth_state import replace_recovery_codes

    with database.transaction() as connection:
        recovery = replace_recovery_codes(connection, keys)[0]
    admin_token = auth.verify(Verification(challenge, "recovery", recovery, "fixture"), "login")
    private_json(
        directory / "browser.json",
        {
            "email": configuration["accounts"][0],
            "password": configuration["password"],
            "admin_token": admin_token,
            "web_port": configuration["web_port"],
        },
    )

    class Inventory:
        def call(self, values: dict[str, Any]) -> dict[str, Any]:
            if values["operation"] == "inventory":
                return {
                    "credentials": configuration["accounts"],
                    "storage": configuration["accounts"],
                }
            raise ServiceError("fixture_admin_mutations_disabled", 403)

    class NoRelay:
        def perform(self, configuration: Any, message: Any) -> None:
            raise ServiceError("fixture_provider_delivery_disabled", 403)

    port = configuration["web_port"]
    settings = Settings(
        f"http://localhost:{port}",
        ROOT / "frontend/dist",
        8192,
        1,
        4,
        12,
        webmail_enabled=True,
        webmail_allowed_mailboxes=tuple(configuration["accounts"]),
    )
    app = Services(
        settings,
        auth,
        Mailboxes((database, Inventory(), time.time)),
        WorkPool(1, 12),
        WorkPool(4, 12),
        Relay(RelayDependencies(database, keys, NoRelay(), time.time)),
    )
    tls = ssl.create_default_context(cafile=directory / "ca.pem")
    app.webmail = MailServices(auth, ImapTransport("127.0.0.1", configuration["imap_port"], tls))
    app.webmail.sending.submission = Submission("127.0.0.1", configuration["submission_port"], tls)
    uvicorn.run(create_app(app), host="127.0.0.1", port=port, access_log=False, server_header=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "serve", "observe"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--messages", type=int, default=1000)
    parser.add_argument("--duration", type=int, default=120)
    args = parser.parse_args()
    if not 1 <= args.messages <= 1000:
        parser.error("messages must be between 1 and 1000 per synthetic mailbox")
    if args.action == "prepare":
        prepare(args.directory, args.messages)
    elif args.action == "serve":
        serve(args.directory)
    else:
        from backend.database import Database
        from host.webmail_retention import TrashRetention, observe

        control = json.loads((args.directory / "control.json").read_text())
        if control["scope"] != "isolated-synthetic-webmail":
            raise RuntimeError("An isolated fixture record is required")
        application = Path(control["application"])
        configuration = json.loads((application / "fixture.json").read_text())
        database = Database(application / "application.db", configuration["installation_id"])
        retention = TrashRetention(database)
        deadline, cycles = time.monotonic() + args.duration, 0
        while time.monotonic() < deadline:
            retention.reconcile(
                observe(Path(control["maddy_root"]) / "data/imapsql.db"), time.time()
            )
            cycles += 1
            time.sleep(5)
        print(json.dumps({"scope": control["scope"], "maintenance_cycles": cycles}))


if __name__ == "__main__":
    main()
