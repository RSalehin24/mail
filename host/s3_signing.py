"""AWS Signature Version 4 for the small S3 surface used by private R2 backups."""

import hashlib
import hmac
from dataclasses import dataclass, field
from urllib.parse import quote

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class S3Credentials:
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)


@dataclass(frozen=True)
class S3Request:
    method: str
    path: str
    headers: dict[str, str]
    query: str = ""
    payload_sha256: str = EMPTY_SHA256


def canonical_query(parameters: dict[str, str]) -> str:
    encoded = ((quote(key, safe="-_.~"), quote(value, safe="-_.~"))
               for key, value in parameters.items())
    return "&".join(key + "=" + value for key, value in sorted(encoded))


class SigV4:
    def __init__(self, credentials: S3Credentials, scope: tuple[str, str]) -> None:
        self.credentials = credentials
        self.region, self.service = scope

    def authorization(self, request: S3Request) -> str:
        headers = {key.lower(): " ".join(value.split()) for key, value in request.headers.items()}
        names = ";".join(sorted(headers))
        canonical = "\n".join((
            request.method, request.path, request.query,
            "".join(key + ":" + headers[key] + "\n" for key in sorted(headers)),
            names, request.payload_sha256,
        ))
        stamp = headers["x-amz-date"]
        scope = "/".join((stamp[:8], self.region, self.service, "aws4_request"))
        message = "\n".join((
            "AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest(),
        ))
        signature = hmac.new(self._key(stamp[:8]), message.encode(), hashlib.sha256).hexdigest()
        return (
            "AWS4-HMAC-SHA256 Credential=" + self.credentials.access_key_id + "/" + scope
            + ", SignedHeaders=" + names + ", Signature=" + signature
        )

    def _key(self, date: str) -> bytes:
        key = ("AWS4" + self.credentials.secret_access_key).encode()
        for value in (date, self.region, self.service, "aws4_request"):
            key = hmac.new(key, value.encode(), hashlib.sha256).digest()
        return key
