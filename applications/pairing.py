"""Domain scoped, revocable integration credentials bound to verified client certificates."""

import base64
import json
import secrets
import time
import uuid
from typing import Any

from applications import CAPABILITIES, INTEGRATION_VERSION, VERSION
from applications.certificates import csr, issue, pem_key, private_key
from backend.database import Database
from backend.secrets import SecretKeys
from shared.errors import ServiceError
from shared.installation import site

CERTIFICATE_SECONDS = 30 * 86400


def compatible(values: dict[str, Any]) -> None:
    if values.get("api_version") != INTEGRATION_VERSION or not set(CAPABILITIES).issubset(
        values.get("capabilities", [])
    ):
        raise ServiceError("integration_upgrade_required", 409)


class PairingAuthority:
    def __init__(self, database: Database, keys: SecretKeys, ca_key: str, ca_pem: str) -> None:
        self.database, self.keys = database, keys
        self.ca_key, self.ca_pem = ca_key, ca_pem

    def enrollment(self, origin: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        key = private_key()
        cert, fingerprint = issue(
            self.ca_key,
            self.ca_pem,
            csr(key, "enrollment"),
            "enrollment",
            300,
        )
        now = time.time()
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM integration_enrollments")
            connection.execute(
                "INSERT INTO integration_enrollments VALUES(?,?,?,0)",
                (self.keys.digest(token, "enrollment"), fingerprint, now + 300),
            )
        invitation = {
            "api_version": INTEGRATION_VERSION,
            "capabilities": list(CAPABILITIES),
            "origin": origin,
            "ca": self.ca_pem,
            "certificate": cert,
            "key": pem_key(key),
            "token": token,
            "domain": site().domain,
            "management_id": self.database.identity,
            "expires": now + 300,
        }
        encoded = base64.urlsafe_b64encode(json.dumps(invitation).encode()).decode()
        return {"enrollment_token": "mm1." + encoded, "expires": now + 300}

    def enroll(self, payload: dict[str, Any], fingerprint: str) -> dict[str, Any]:
        compatible(payload)
        try:
            identifier = str(uuid.UUID(payload["installation_id"]))
            token, request = payload["token"], payload["csr"]
            if not isinstance(token, str) or not isinstance(request, str):
                raise ValueError("Invalid enrollment")
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ServiceError("invalid_enrollment", 400) from error
        now = time.time()
        with self.database.transaction() as connection:
            enrollment = connection.execute(
                "SELECT * FROM integration_enrollments WHERE token_hash=? AND fingerprint=?",
                (self.keys.digest(token, "enrollment"), fingerprint),
            ).fetchone()
            if enrollment is None or enrollment["used"] or enrollment["expires"] <= now:
                raise ServiceError("enrollment_expired_or_used", 403)
            if connection.execute(
                "SELECT 1 FROM integration_credentials WHERE revoked=0 AND expires>? "
                "AND installation_id!=?",
                (now, identifier),
            ).fetchone():
                raise ServiceError("revoke_existing_pairing_first", 409)
            connection.execute(
                "UPDATE integration_enrollments SET used=1 WHERE token_hash=?",
                (enrollment["token_hash"],),
            )
            result = self._credential(connection, identifier, request)
            connection.execute(
                "UPDATE integration_credentials SET revoked=1 WHERE installation_id=? AND id!=?",
                (identifier, result["credential_id"]),
            )
        return result

    def _credential(
        self, connection: Any, installation: str, request: str, replaces: str | None = None
    ) -> dict[str, Any]:
        identifier = uuid.uuid4().hex
        bearer = identifier + "." + secrets.token_urlsafe(32)
        certificate, fingerprint = issue(
            self.ca_key,
            self.ca_pem,
            request,
            identifier,
            CERTIFICATE_SECONDS,
        )
        now = time.time()
        connection.execute(
            "INSERT INTO integration_credentials VALUES(?,?,?,?,?,?,0,?,?,?)",
            (
                identifier,
                installation,
                site().domain,
                fingerprint,
                self.keys.digest(bearer, "integration"),
                now + CERTIFICATE_SECONDS,
                replaces,
                0 if replaces else 1,
                now,
            ),
        )
        return {
            "api_version": INTEGRATION_VERSION,
            "capabilities": list(CAPABILITIES),
            "version": VERSION,
            "management_id": self.database.identity,
            "domain": site().domain,
            "mail_hostname": site().mail_hostname,
            "credential_id": identifier,
            "bearer": bearer,
            "certificate": certificate,
            "expires": now + CERTIFICATE_SECONDS,
        }

    def authenticate(self, bearer: str, fingerprint: str) -> dict[str, Any]:
        if not fingerprint or len(bearer) > 256:
            raise ServiceError("integration_authentication_required", 401)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM integration_credentials WHERE id=? AND fingerprint=? "
                "AND verifier=? AND revoked=0 AND domain=? AND expires>?",
                (
                    bearer.partition(".")[0],
                    fingerprint,
                    self.keys.digest(bearer, "integration"),
                    site().domain,
                    time.time(),
                ),
            ).fetchone()
            if row is None:
                raise ServiceError("integration_credential_revoked_or_expired", 401)
            return dict(row)

    def rotate(self, credential: dict[str, Any], request: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT 1 FROM integration_credentials WHERE id=? AND revoked=0 "
                "AND activated=1 AND expires>?",
                (credential["id"], time.time()),
            ).fetchone()
            if current is None:
                raise ServiceError("integration_credential_revoked_or_expired", 401)
            return self._credential(
                connection,
                credential["installation_id"],
                request,
                credential["id"],
            )

    def activate(self, credential: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            # Recheck under the mutation transaction: revocation can race initial authorization.
            current = connection.execute(
                "SELECT * FROM integration_credentials WHERE id=? AND revoked=0 AND expires>?",
                (credential["id"], time.time()),
            ).fetchone()
            if current is None:
                raise ServiceError("integration_credential_revoked_or_expired", 401)
            if not current["activated"] and current["replaces"]:
                parent = connection.execute(
                    "SELECT 1 FROM integration_credentials WHERE id=? AND revoked=0 AND expires>?",
                    (current["replaces"], time.time()),
                ).fetchone()
                if parent is None:
                    raise ServiceError("integration_credential_revoked_or_expired", 401)
            # A prepared replacement is usable to activate itself, never for mailbox operations.
            connection.execute(
                "UPDATE integration_credentials SET activated=1 WHERE id=? AND revoked=0",
                (credential["id"],),
            )
            if credential["replaces"]:
                connection.execute(
                    "UPDATE integration_credentials SET revoked=1 WHERE id=?",
                    (credential["replaces"],),
                )

    def revoke(self, identifier: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE integration_credentials SET revoked=1 WHERE installation_id IN "
                "(SELECT installation_id FROM integration_credentials WHERE id=?)",
                (identifier,),
            )
            connection.execute("UPDATE application_state SET cleanup_enabled=0")

    def status(self) -> list[dict[str, Any]]:
        with self.database.transaction() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id,installation_id,domain,expires,revoked,activated,created "
                    "FROM integration_credentials ORDER BY created DESC LIMIT 30"
                )
            ]
