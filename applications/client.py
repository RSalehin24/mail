"""Certificate verified integration client with bounded I/O and encrypted local pairing state."""

import base64
import hashlib
import http.client
import json
import ssl
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from applications import CAPABILITIES, INTEGRATION_VERSION
from applications.certificates import csr, pem_key, private_key
from applications.pairing import compatible
from backend.database import Database
from backend.secrets import SecretKeys
from shared.errors import ServiceError
from shared.installation import hostname, site


class Connections:
    """Bounded HTTPS keep-alive pool; credentials remain checked on every request."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.capacity = threading.BoundedSemaphore(16)
        self.idle: dict[str, list[tuple[http.client.HTTPSConnection, float, float]]] = {}
        self.closed = False

    @staticmethod
    def key(values: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps([values[name] for name in ("origin", "ca", "certificate", "key")]).encode()
        ).hexdigest()

    def take(self, values: dict[str, Any]) -> tuple[http.client.HTTPSConnection, float]:
        if not self.capacity.acquire(timeout=3):
            raise ServiceError("integration_busy", 503)
        try:
            key, now = self.key(values), time.monotonic()
            with self.lock:
                if self.closed:
                    raise ServiceError("management_connection_unavailable", 503)
                for stale in list(self.idle):
                    if stale != key:
                        for connection, _, _ in self.idle.pop(stale):
                            connection.close()
                entries = self.idle.setdefault(key, [])
                while entries:
                    connection, used, created = entries.pop()
                    if now - used < 2 and now - created < 30:
                        return connection, created
                    connection.close()
            return connection_for(values), now
        except BaseException:
            self.capacity.release()
            raise

    def put(
        self,
        values: dict[str, Any],
        connection: http.client.HTTPSConnection,
        created: float,
        reusable: bool,
    ) -> None:
        with self.lock:
            if reusable and not self.closed and connection.sock is not None:
                self.idle.setdefault(self.key(values), []).append(
                    (connection, time.monotonic(), created)
                )
            else:
                connection.close()
        self.capacity.release()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for entries in self.idle.values():
                for connection, _, _ in entries:
                    connection.close()
            self.idle.clear()


def connection_for(values: dict[str, Any]) -> http.client.HTTPSConnection:
    endpoint = urlsplit(values["origin"])
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
    ):
        raise ServiceError("invalid_integration_endpoint", 400)
    hostname(endpoint.hostname)
    context = ssl.create_default_context(cadata=values["ca"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix="mail-pair-") as temporary:
        for filename, field in (("client.pem", "certificate"), ("client.key", "key")):
            target = Path(temporary) / filename
            target.write_text(values[field])
            target.chmod(0o600)
        context.load_cert_chain(
            str(Path(temporary) / "client.pem"), str(Path(temporary) / "client.key")
        )
        return http.client.HTTPSConnection(
            endpoint.hostname,
            endpoint.port or 443,
            context=context,
            timeout=3,
        )


def exchange(
    values: dict[str, Any], path: str, body: dict[str, Any] | None, pool: Connections | None = None
) -> dict[str, Any]:
    connection, created = pool.take(values) if pool else (connection_for(values), 0.0)
    reusable = False
    try:
        headers = {
            "Content-Type": "application/json",
            "X-Mail-Integration-Version": str(INTEGRATION_VERSION),
        }
        if values.get("bearer"):
            headers["Authorization"] = "Bearer " + values["bearer"]
        payload = None if body is None else json.dumps(body).encode()
        connection.request(
            "GET" if body is None else "POST", "/integration/v1/" + path, payload, headers
        )
        response = connection.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ServiceError("invalid_integration_response", 502)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid response")
        reusable = not response.will_close
        if response.status == 404 and "error" not in result:
            raise ServiceError("integration_upgrade_required", 409)
        if response.status >= 400:
            raise ServiceError(str(result.get("error", "integration_unavailable")), response.status)
        return result
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise ServiceError("management_connection_unavailable", 503) from error
    finally:
        if pool:
            pool.put(values, connection, created, reusable)
        else:
            connection.close()


class ManagementClient:
    def __init__(self, database: Database, keys: SecretKeys) -> None:
        self.database, self.keys = database, keys
        self.connections = Connections()

    def close(self) -> None:
        self.connections.close()

    def load(self) -> dict[str, Any]:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT secret FROM remote_pairing WHERE id=1").fetchone()
        if row is None:
            raise ServiceError("mail_management_pairing_required", 503)
        value = json.loads(self.keys.open(row[0], "remote-pairing"))
        if not isinstance(value, dict):
            raise ServiceError("invalid_pairing_state", 503)
        return value

    def save(self, value: dict[str, Any]) -> None:
        secret = self.keys.seal(json.dumps(value), "remote-pairing")
        with self.database.transaction() as connection:
            connection.execute("INSERT OR REPLACE INTO remote_pairing VALUES(1,?)", (secret,))

    def enroll(self, token: str) -> dict[str, Any]:
        import time

        if not token.startswith("mm1.") or len(token) > 24000:
            raise ServiceError("invalid_enrollment_token", 400)
        try:
            invitation = json.loads(base64.urlsafe_b64decode(token[4:]))
            compatible(invitation)
            if invitation["expires"] <= time.time() or invitation["domain"] != site().domain:
                raise ServiceError("enrollment_expired_or_wrong_domain", 400)
            key = private_key()
            result = exchange(
                invitation,
                "enroll",
                {
                    "api_version": INTEGRATION_VERSION,
                    "capabilities": list(CAPABILITIES),
                    "installation_id": self.database.identity,
                    "token": invitation["token"],
                    "csr": csr(key, self.database.identity),
                },
            )
            compatible(result)
            if (
                result["management_id"] != invitation["management_id"]
                or result["domain"] != site().domain
                or result["mail_hostname"] != site().mail_hostname
            ):
                raise ServiceError("paired_installation_identity_mismatch", 409)
            self.save(
                {
                    **result,
                    "origin": invitation["origin"],
                    "ca": invitation["ca"],
                    "key": pem_key(key),
                }
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ServiceError("invalid_enrollment_token", 400) from error
        return self.status()

    def call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        values = self.load()
        compatible(values)
        if values["domain"] != site().domain:
            raise ServiceError("paired_domain_mismatch", 503)
        return exchange(values, path, body, self.connections)

    def identify(self, email: str) -> dict[str, Any]:
        result = self.call("identity", {"email": email})
        if (
            result.get("email") != email
            or not isinstance(result.get("id"), str)
            or len(result["id"]) != 32
            or type(result.get("revision")) is not int
            or type(result.get("inbox_validity")) is not int
        ):
            raise ServiceError("invalid_integration_identity", 502)
        return result

    def change_password(self, identity: Any, password: str, new_password: str) -> dict[str, Any]:
        remote = self.identify(identity.email)
        if remote["id"] != identity.id or remote["revision"] != identity.authority_revision:
            raise ServiceError("mailbox_security_revision_changed", 409)
        return self.call(
            "password",
            {
                "email": identity.email,
                "mailbox_id": identity.id,
                "revision": remote["revision"],
                "password": password,
                "new_password": new_password,
                "request_id": str(uuid.uuid4()),
            },
        )

    def rotate(self) -> dict[str, Any]:
        before = self.load()
        key = private_key()
        result = self.call("credentials/rotate", {"csr": csr(key, self.database.identity)})
        compatible(result)
        # Persist before activating; interruption can resume with the prepared credential.
        self.save({**before, **result, "key": pem_key(key)})
        return self.activate()

    def activate(self) -> dict[str, Any]:
        self.call("credentials/activate", {})
        return self.status()

    def status(self) -> dict[str, Any]:
        values = self.load()
        result = self.call("status")
        compatible(result)
        if result["management_id"] != values["management_id"]:
            raise ServiceError("paired_installation_identity_mismatch", 503)
        return {
            **result,
            "connected": True,
            "origin": values["origin"],
            "expires": values["expires"],
        }

    def unpair(self) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM remote_pairing")
            connection.execute("DELETE FROM mailbox_sessions")
            connection.execute("UPDATE application_state SET cleanup_enabled=0")
