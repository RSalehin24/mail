"""Read-only Google uptime-policy verification; failures never refresh a success claim."""

import base64
import json
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.installation import site

CONFIG = Path("/etc/mail-admin/independent-monitor.json")
OBSERVATION = Path("/var/lib/mail-admin-alerts/external-monitoring.json")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, request: Any, response: Any, code: int, message: str, headers: Any, url: str
    ) -> None:
        raise ServiceError("monitor_redirect_rejected", 503)


def open_google(request: urllib.request.Request) -> Any:
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=10)


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def access_token(credentials: dict[str, Any], now: float) -> str:
    header = encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = encode(
        json.dumps(
            {
                "iss": credentials["client_email"],
                "scope": "https://www.googleapis.com/auth/monitoring.read",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": int(now),
                "exp": int(now) + 3600,
            }
        ).encode()
    )
    unsigned = (header + "." + payload).encode()
    with tempfile.TemporaryDirectory(prefix="mail-monitor-key-") as directory:
        key = Path(directory) / "key.pem"
        key.write_text(credentials["private_key"])
        key.chmod(0o600)
        signed = subprocess.run(  # noqa: S603 -- fixed binary and private generated key path.
            ["/usr/bin/openssl", "dgst", "-sha256", "-sign", str(key)],
            input=unsigned,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout
    assertion = unsigned.decode() + "." + encode(signed)
    body = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
    ).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with open_google(request) as response:  # noqa: S310 -- fixed Google HTTPS endpoint.
        result = json.load(response)
    return str(result["access_token"])


def get_resource(name: str, token: str, project: str) -> dict[str, Any]:
    if not re.fullmatch(r"projects/[a-z0-9-]+", project) or not re.fullmatch(
        re.escape(project)
        + r"/(alertPolicies|uptimeCheckConfigs|notificationChannels)/[A-Za-z0-9_-]+",
        name,
    ):
        raise ServiceError("invalid_monitor_resource", 503)
    request = urllib.request.Request(
        "https://monitoring.googleapis.com/v3/" + name, headers={"Authorization": "Bearer " + token}
    )
    with open_google(request) as response:  # noqa: S310 -- fixed Google HTTPS endpoint.
        result: dict[str, Any] = json.load(response)
    if result.get("name") != name:
        raise ServiceError("monitor_resource_mismatch", 503)
    return result


def verify(configuration: dict[str, Any], token: str) -> bool:
    project = configuration["project"]
    checks = [get_resource(name, token, project) for name in configuration["checks"]]
    ports = set()
    for check in checks:
        if check.get("monitoredResource", {}).get("labels", {}).get("host") != site().mail_hostname:
            raise ServiceError("monitor_target_mismatch", 503)
        http = check.get("httpCheck", {})
        tcp = check.get("tcpCheck", {})
        if http.get("port") == 443 and http.get("useSsl") and http.get("validateSsl"):
            ports.add(443)
        if tcp.get("port") == 993:
            ports.add(993)
    if ports != {443, 993}:
        raise ServiceError("monitor_checks_incomplete", 503)
    policies = [get_resource(name, token, project) for name in configuration["policies"]]
    channels = set(configuration["channels"])
    if not policies or not channels:
        raise ServiceError("monitor_alerts_incomplete", 503)
    for check in checks:
        check_id = check["name"].rsplit("/", 1)[-1]
        related = [p for p in policies if check_id in json.dumps(p.get("conditions", []))]
        if not related or not any(
            p.get("enabled") is True and channels <= set(p.get("notificationChannels", []))
            for p in related
        ):
            return False
    return all(get_resource(name, token, project).get("enabled") is True for name in channels)


def observe(configuration: dict[str, Any], path: Path = OBSERVATION) -> dict[str, Any]:
    now = time.time()
    previous = protected_json(path) if path.exists() else {}
    result: dict[str, Any] = {
        "provider": "Google Cloud Monitoring",
        "configured": True,
        "observed_at": now,
        "last_verified_at": previous.get("last_verified_at"),
    }
    try:
        credentials = protected_json(Path(configuration["credential_reference"]))
        result["enabled"] = verify(configuration, access_token(credentials, now))
        result["outcome"] = "verified" if result["enabled"] else "disabled"
        result["last_verified_at"] = now
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ServiceError):
        result.update(enabled=None, outcome="verification_failed")
    atomic_json(path, result)
    return result


def main() -> int:
    result = observe(protected_json(CONFIG))
    return 0 if result["outcome"] == "verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
