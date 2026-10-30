"""Isolated container startup/setup checks. These do not qualify Ubuntu or 1 GB host capacity."""

# Fixture assertions, private tmpfs and a fingerprint-pinned bootstrap certificate only.
# ruff: noqa: S101, S108, S501

import argparse
import hashlib
import json
import socket
import ssl
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pyotp


def docker(*args: str) -> str:
    return subprocess.check_output(("/usr/bin/docker", *args), text=True, timeout=60).strip()  # noqa: S603


def smoke(kind: str, image: str) -> dict:
    identifier = uuid.uuid4().hex
    volume, container = "mail-split-state-" + identifier, "mail-split-smoke-" + identifier
    name = kind + ".fixture.test"
    docker("volume", "create", "--label", "mail-admin.fixture=true", volume)
    try:
        token = docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            volume + ":/state",
            image,
            "python",
            "-m",
            "applications.bootstrap",
            kind,
            "/state",
            name,
            "--resources",
            json.dumps(
                {
                    "hash_memory_kib": 8192,
                    "hash_parallelism": 1,
                    "http_concurrency": 4,
                    "operation_timeout": 15,
                }
            ),
        )
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            volume + ":/state",
            image,
            "python",
            "-c",
            "import os; from pathlib import Path; "
            "[os.chown(p,10001,10001) "
            "for p in [Path('/state'),*Path('/state').rglob('*')] "
            "if not p.is_symlink()]",
        )
        material = json.loads(
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "10001:10001",
                "--volume",
                volume + ":/state",
                image,
                "python",
                "-c",
                "import json; from pathlib import Path; "
                "print(json.dumps({name:Path('/state',name).read_text() "
                "for name in ['tls/active/fullchain.pem','tls/active/privkey.pem',"
                "'bootstrap-fingerprint.txt']}))",
            )
        )
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--label",
            "mail-admin.fixture=true",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "256m",
            "--pids-limit",
            "100",
            "--tmpfs",
            "/tmp:size=33554432,mode=1777",
            "--user",
            "10001:10001",
            "--publish",
            "127.0.0.1::8443",
            "--volume",
            volume + ":/state",
            image,
        )
        inspected = json.loads(docker("inspect", container))[0]
        port = int(inspected["NetworkSettings"]["Ports"]["8443/tcp"][0]["HostPort"])
        # Pin the private bootstrap certificate before sending setup secrets.
        deadline = time.monotonic() + 20
        while True:
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                with socket.create_connection(("127.0.0.1", port), timeout=2) as plain:
                    with context.wrap_socket(plain, server_hostname=name) as secured:
                        actual = hashlib.sha256(secured.getpeercert(binary_form=True)).hexdigest()
                if actual != material["bootstrap-fingerprint.txt"].strip():
                    raise RuntimeError("Bootstrap fingerprint mismatch")
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        with httpx.Client(
            base_url=f"https://127.0.0.1:{port}",
            verify=False,
            headers={"Host": name, "Origin": "https://" + name},
            timeout=10,
        ) as client:

            def post(path, body):
                response = client.post("/api" + path, json=body)
                if response.status_code != 200:
                    raise RuntimeError(
                        kind + " " + path + ": " + str(response.status_code) + " " + response.text
                    )
                return response.json()

            assert client.get("/api/status").status_code == 200
            assert client.get("/api/installation").json()["kind"] == kind
            assert client.get("/api/session").status_code == 401
            post(
                "/installation/configure",
                {
                    "token": token,
                    "domain": "fixture.test",
                    "hostname": name,
                    "mail_hostname": name,
                    "admin_email": "admin@fixture.test",
                    "relay_host": "relay.fixture.test",
                    "relay_username": "synthetic",
                    "relay_password": "synthetic-credential",
                    "spf_value": "v=spf1 -all",
                    "backup_destination": "/var/backups/mail-" + kind,
                    "fullchain": material["tls/active/fullchain.pem"],
                    "private_key": material["tls/active/privkey.pem"],
                },
            )
            enrollment = post("/setup", {"token": token})
            initialized = post(
                "/setup",
                {
                    "token": token,
                    "password": "synthetic master password",
                    "code": pyotp.TOTP(enrollment["secret"]).now(),
                },
            )
            challenge = post(
                "/login", {"email": "admin@fixture.test", "password": "synthetic master password"}
            )
            post(
                "/2fa/verify",
                {
                    "challenge": challenge["challenge"],
                    "factor": "recovery",
                    "code": initialized["recovery_codes"][0],
                },
            )
            assert client.get("/api/session").json()["role"] == "administrator"
            pairing = client.get("/api/pairing")
            assert pairing.status_code == 200
            if kind == "webmail":
                assert pairing.json()["connected"] is False
                assert pairing.json()["reason"] == "mail_management_pairing_required"
                assert client.get("/api/accounts").status_code == 404
            else:
                accounts = client.get("/api/accounts")
                assert (
                    accounts.status_code == 503 and accounts.json()["error"] == "helper_unavailable"
                )
            assert client.get("/api/webmail/folders").status_code in {404, 503}
            assert client.get("/").status_code == 200
        result = json.loads(docker("inspect", container))[0]
        assert not result["State"]["OOMKilled"]
        return {
            "kind": kind,
            "image": result["Image"],
            "result": "pass",
            "independent_startup": True,
            "setup_and_required_mfa": True,
            "disconnection_is_explicit": True,
            "mailbox_access_disabled": True,
            "local_functional_container_limit_mib": 256,
            "capacity_approved": False,
        }
    finally:
        subprocess.run(  # noqa: S603 -- exact private fixture container cleanup.
            ("/usr/bin/docker", "rm", "-f", container), capture_output=True, check=False, timeout=30
        )  # noqa: S603
        docker("volume", "rm", volume)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--management-image", required=True)
    parser.add_argument("--webmail-image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {
        "scope": "local isolated container functional checks; not two-server qualification",
        "applications": [
            smoke(kind, getattr(args, kind + "_image")) for kind in ("management", "webmail")
        ],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("Independent startup, setup, MFA, role isolation and disconnected operation passed.")


if __name__ == "__main__":
    main()
