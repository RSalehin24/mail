"""AES-GCM fields and domain-separated keyed verifiers; keys never enter SQLite."""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shared.errors import ServiceError
from shared.files import protected_bytes, protected_json


@dataclass(frozen=True)
class SecretKeys:
    encryption: bytes
    verifier: bytes

    def __post_init__(self) -> None:
        if len(self.encryption) != 32 or len(self.verifier) != 32:
            raise ServiceError("invalid_secret_key", 503)

    def seal(self, value: str, purpose: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.encryption).encrypt(nonce, value.encode(), purpose.encode())
        return base64.b64encode(nonce + ciphertext).decode()

    def open(self, value: str, purpose: str) -> str:
        try:
            payload = base64.b64decode(value, validate=True)
            plaintext = AESGCM(self.encryption).decrypt(
                payload[:12], payload[12:], purpose.encode()
            )
            return plaintext.decode()
        except (ValueError, InvalidTag, UnicodeError) as error:
            raise ServiceError("secret_decryption_failed", 503) from error

    def digest(self, value: str, purpose: str) -> str:
        return hmac.new(
            self.verifier, (purpose + "\0" + value).encode(), hashlib.sha256
        ).hexdigest()

    @classmethod
    def load(cls, directory: Path, marker: Path) -> "SecretKeys":
        record = protected_json(marker)
        instance = cls(
            protected_bytes(directory / "encryption.key"),
            protected_bytes(directory / "verifier.key"),
        )
        for name, value in (("encryption", instance.encryption), ("verifier", instance.verifier)):
            if record.get(name + "_sha256") != hashlib.sha256(value).hexdigest():
                raise ServiceError("key_identity_mismatch", 503)
        return instance
