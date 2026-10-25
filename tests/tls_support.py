import datetime
import ipaddress
import socketserver
import ssl
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def openssl_executable():
    executable = Path('/opt/homebrew/opt/openssl@3/bin/openssl')
    return str(executable) if executable.exists() else '/usr/bin/openssl'


class TlsHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with self.server.context.wrap_socket(self.request, server_side=True):
                pass
        except (OSError, ssl.SSLError):
            self.server.rejected += 1


class TlsPeer:
    def __init__(self, certificate):
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), TlsHandler)
        self.server.daemon_threads = True
        self.server.rejected = 0
        self.server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server.context.load_cert_chain(
            *(certificate / name for name in ("fullchain.pem", "privkey.pem"))
        )
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True
        )
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class CertificateFixture:
    def __init__(self, directory: Path, lifetime_days=7):
        self.directory = directory
        self.lifetime_days = lifetime_days
        self.key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "Mail Admin isolated fixture CA")]
        )
        now = datetime.datetime.now(datetime.UTC)
        self.ca = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=None, decipher_only=None,
            ), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self.key.public_key()), False)
            .sign(self.key, hashes.SHA256())
        )
        self.trust = directory / "fixture-ca.pem"
        self.trust.write_bytes(self.ca.public_bytes(serialization.Encoding.PEM))

    def issue(self, name="valid", hostname="mail.rsalehin24.me", expired=False, loopback=True):
        directory = self.directory / name
        directory.mkdir(mode=0o700)
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.UTC)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
            .issuer_name(self.ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=2))
            .not_valid_after(now + datetime.timedelta(days=-1 if expired else self.lifetime_days))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(hostname),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ] if loopback else [x509.DNSName(hostname)]
                ),
                False,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()), False
            )
            .sign(self.key, hashes.SHA256())
        )
        (directory / "fullchain.pem").write_bytes(
            certificate.public_bytes(serialization.Encoding.PEM)
            + self.ca.public_bytes(serialization.Encoding.PEM)
        )
        (directory / "privkey.pem").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        (directory / "privkey.pem").chmod(0o600)
        return directory
