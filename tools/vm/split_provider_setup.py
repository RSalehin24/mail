"""Prepare a private certificate authority, DNS and relay for the synthetic Docker network."""

# Exact private fixture argv; secrets travel on stdin to its newly created named volume.
# ruff: noqa: S603, S108
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from applications.certificates import ca, pem_key, private_key
from tools.simulate_split import ROOT, docker


def prepare(root: Path, image: str) -> None:
    state = json.loads((root / "state.json").read_text())
    if "provider" in state:
        raise ValueError("A provider is already installed in this simulation")
    material = root / "tls"
    material.mkdir(mode=0o700)
    key, certificate = ca("Isolated split mail simulation")
    issuer = x509.load_pem_x509_certificate(certificate.encode())
    signing = serialization.load_pem_private_key(key.encode(), None)
    files = {"ca.pem": certificate}
    for label, names in (
        ("management", ["manage.split.test", "mail.split.test"]),
        ("webmail", ["webmail.split.test"]),
        ("relay", ["relay.split.test"]),
    ):
        leaf_key, now = private_key(), datetime.now(UTC)
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(issuer.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(name) for name in names]), False
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .sign(signing, hashes.SHA256())
        )
        files[label + ".pem"] = leaf.public_bytes(serialization.Encoding.PEM).decode() + certificate
        files[label + ".key"] = pem_key(leaf_key)
    for name, value in files.items():
        (material / name).write_text(value)
        (material / name).chmod(0o600)
    hosts = {
        "manage.split.test": state["addresses"]["management"],
        "mail.split.test": state["addresses"]["management"],
        "webmail.split.test": state["addresses"]["webmail"],
        "relay.split.test": state["addresses"]["provider"],
    }
    files["provider.json"] = json.dumps({"hosts": hosts})
    (root / "provider.json").write_text(files["provider.json"])
    image_id = json.loads(docker("image", "inspect", image))[0]["Id"]
    name = "mail-split-provider-" + state["id"]
    docker("volume", "create", "--label", "mail-admin.fixture=true", name)
    writer = (
        "import json,os,sys;from pathlib import Path;p=Path('/fixture');os.chown(p,10001,10001);"
        "[(p/n).write_text(v) for n,v in json.load(sys.stdin).items()];"
        "[(os.chown(f,10001,10001),f.chmod(0o600)) for f in p.iterdir()]"
    )
    subprocess.run(
        (
            "/usr/bin/docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            name + ":/fixture",
            image_id,
            "python",
            "-c",
            writer,
        ),
        input=json.dumps(files),
        text=True,
        check=True,
        timeout=30,
    )
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--label",
        "mail-admin.fixture=true",
        "--network",
        state["network"],
        "--ip",
        state["addresses"]["provider"],
        "--network-alias",
        "relay.split.test",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1",
        "--pids-limit",
        "100",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--user",
        "10001:10001",
        "--volume",
        name + ":/fixture",
        "--env",
        "PYTHONPATH=/app",
        "--mount",
        "type=bind,src=" + str(ROOT / "tools/vm/split_provider.py") + ",dst=/provider.py,readonly",
        "--entrypoint",
        "python",
        image_id,
        "/provider.py",
    )
    state["provider"] = {"container": name, "volume": name, "image": image_id}
    (root / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    print("Private DNS, TLS and synthetic SMTP relay prepared; no public mail is sent.")
