"""Seed ten explicitly isolated qualification mailboxes through certificate verified IMAP."""

import argparse
import imaplib
import json
import os
import re
import shlex
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.simulation_network import private_dns  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    if args.config.stat().st_mode & 0o077:
        parser.error("Use a mode-0600 synthetic credential file")
    config = json.loads(args.config.read_text())
    if (
        config.get("scope") != "isolated-two-host-mail-qualification"
        or len(config.get("accounts", [])) != 10
        or not all(email.startswith("qualify-") for email in config["accounts"])
    ):
        parser.error("Ten explicit synthetic qualification mailboxes required")
    target = config["management_ssh"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", target):
        parser.error("Invalid SSH target")
    probe = """import json,pathlib
host=json.loads(pathlib.Path('/etc/mail-management/host.json').read_text())
if not host.get('qualification_only'): raise SystemExit('Not a qualification installation')
print(pathlib.Path(host['root'],'site.json').read_text())
"""
    ssh = ["/usr/bin/ssh"]
    if config.get("ssh_config"):
        if Path(config["ssh_config"]).stat().st_mode & 0o077:
            raise ValueError("Use a private SSH configuration")
        ssh += ["-F", config["ssh_config"]]
    observed = json.loads(
        subprocess.check_output(  # noqa: S603 -- fixed read-only SSH probe.
            (
                *ssh,
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                target,
                "sudo -n /usr/bin/python3 -c " + shlex.quote(probe),
            ),
            text=True,
            timeout=15,
        )
    )
    hostname = observed["mail_hostname"]
    context = ssl.create_default_context()
    with private_dns(config):
        seed(config, observed, hostname, context)


def seed(config, observed, hostname, context):
    total = 0
    for email in config["accounts"]:
        if email.partition("@")[2] != observed["domain"]:
            raise ValueError("Synthetic address belongs to another domain")
        deadline = time.monotonic() + 180
        with imaplib.IMAP4_SSL(hostname, 993, ssl_context=context, timeout=9) as mail:
            mail.login(email, config["password"])
            for index in range(1000):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Mailbox seeding deadline exceeded")
                message = EmailMessage()
                message["From"] = config["accounts"][(total // 1000 + 1) % 10]
                message["To"] = email
                message["Subject"] = f"Synthetic qualification message {index}"
                message.set_content("Synthetic searchable body. " * 30)
                message.add_attachment(
                    b"isolated synthetic attachment",
                    maintype="application",
                    subtype="octet-stream",
                    filename="synthetic.bin",
                )
                status, _ = mail.append("INBOX", "", None, message.as_bytes())
                if status != "OK":
                    raise RuntimeError("Synthetic APPEND failed")
                total += 1
    print(json.dumps({"messages": total, "users": 10, "method": "individual verified IMAP APPEND"}))


if __name__ == "__main__":
    os.umask(0o077)
    main()
