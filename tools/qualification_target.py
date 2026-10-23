"""Observe the authenticated GCE target and the actual negotiated SSH host key."""

import base64
import json
import re
import shlex
import subprocess

METADATA_PROBE = """import json,urllib.request
def value(path):
 request=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/'+path,
                              headers={'Metadata-Flavor':'Google'})
 with urllib.request.urlopen(request,timeout=5) as response:
  if response.headers.get('Metadata-Flavor')!='Google':
   raise RuntimeError('Untrusted metadata response')
  payload=response.read(4097)
  if len(payload)>4096: raise RuntimeError('Oversized metadata response')
  return payload.decode().strip()
print(json.dumps({'project':value('project/project-id'),
                  'zone':value('instance/zone').rsplit('/',1)[-1],
                  'instance':value('instance/id')}))
"""


def negotiated_key(log: str) -> str:
    matches = re.findall(r"^debug1: Server host key: ssh-ed25519 SHA256:([A-Za-z0-9+/]+)$",
                         log, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("One authenticated ed25519 SSH negotiation is required")
    decoded = base64.b64decode(matches[0] + "=", validate=True)
    if len(decoded) != 32:
        raise ValueError("Invalid observed SSH fingerprint")
    return decoded.hex()


def target_observation(metadata: dict, key_digest: str) -> dict[str, str]:
    if set(metadata) != {"project", "zone", "instance"} or any(
        not isinstance(value, str) or re.fullmatch(r"[a-zA-Z0-9_-]+", value) is None
        for value in metadata.values()
    ):
        raise ValueError("Invalid authenticated target metadata")
    if re.fullmatch(r"[0-9a-f]{64}", key_digest) is None:
        raise ValueError("Invalid observed SSH key digest")
    identity = "projects/{project}/zones/{zone}/instances/{instance}".format(**metadata)
    return {"target_instance_id": identity, "target_ssh_host_key_sha256": key_digest}


class TargetConnection:
    def __init__(self, host: str, expected: dict[str, str]) -> None:
        if re.fullmatch(r"[a-zA-Z0-9_-]+@[a-zA-Z0-9.-]+", host) is None:
            raise ValueError("Explicit SSH user@host required")
        self.host = host
        self.expected = {key: expected[key] for key in (
            "target_instance_id", "target_ssh_host_key_sha256"
        )}

    def run(self, arguments: tuple[str, ...]) -> tuple[str, str]:
        result = subprocess.run(  # noqa: S603 -- fixed SSH options, validated target, quoted argv.
            ("/usr/bin/ssh", "-v", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
             "-o", "HostKeyAlgorithms=ssh-ed25519", "-o", "ControlMaster=no",
             "-o", "ControlPath=none", "-o", "ConnectTimeout=10", self.host,
             shlex.join(arguments)),
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode:
            raise ValueError("Authenticated target observation failed")
        key = negotiated_key(result.stderr)
        if key != self.expected["target_ssh_host_key_sha256"]:
            raise ValueError("Connected SSH host key differs from the qualified target")
        if len(result.stdout.encode()) > 2 * 1024 * 1024:
            raise ValueError("Oversized target observation")
        return result.stdout.strip(), key

    def observe(self) -> dict[str, str]:
        payload, key = self.run(("python3", "-c", METADATA_PROBE))
        observed = target_observation(json.loads(payload), key)
        if observed != self.expected:
            raise ValueError("Authenticated cloud target differs from the qualified target")
        return observed
