"""Dedicated integration CA and short lived enrollment transport certificates."""

import hashlib
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from shared.errors import ServiceError
from shared.installation import hostname


def private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def fingerprint(certificate: bytes) -> str:
    return hashlib.sha256(certificate).hexdigest()


def ca(name: str) -> tuple[str, str]:
    key = private_key()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return pem_key(key), certificate.public_bytes(serialization.Encoding.PEM).decode()


def csr(key: ec.EllipticCurvePrivateKey, name: str) -> str:
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )


def issue(
    ca_key: str,
    ca_pem: str,
    request: str,
    name: str,
    seconds: int,
    server: bool = False,
) -> tuple[str, str]:
    try:
        signing = serialization.load_pem_private_key(ca_key.encode(), password=None)
        authority = x509.load_pem_x509_certificate(ca_pem.encode())
        pending = x509.load_pem_x509_csr(request.encode())
        public = pending.public_key()
        if not pending.is_signature_valid or not isinstance(public, ec.EllipticCurvePublicKey):
            raise ValueError("Invalid CSR")
        if not isinstance(signing, ec.EllipticCurvePrivateKey) or public.key_size < 256:
            raise ValueError("Unsupported key")
    except (ValueError, TypeError) as error:
        raise ServiceError("invalid_certificate_request", 400) from error
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(authority.subject)
        .public_key(public)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(seconds=30))
        .not_valid_after(now + timedelta(seconds=seconds))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=True,
        )
    )
    if server:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(hostname(name))]), critical=False
        )
    certificate = builder.sign(signing, hashes.SHA256())
    return (
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        fingerprint(certificate.public_bytes(serialization.Encoding.DER)),
    )
