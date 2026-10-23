"""Only fixed allowlisted host observations; missing/stale state is explicitly unknown."""

import os
import time
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import protected_json

OBSERVATION_FIELDS = frozenset({
    'observed_at', 'outcome', 'cutoff', 'verified', 'available', 'restore_passed', 'expiry',
    'desired_generation', 'availability_observed_at',
})
CONSUMER_FIELDS = frozenset({
    'intent', 'state', 'reason', 'active_generation', 'served_sha256', 'served_expiry',
})


def host_status() -> dict[str, Any]:
    now = time.time()
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, _, value = line.partition(":")
        if name in {"MemTotal", "MemAvailable"}:
            memory[name] = int(value.split()[0]) * 1024
    disk = os.statvfs("/opt/maddy/data")
    return {
        "observed_at": now,
        "scope": "host",
        "memory_total_bytes": memory.get("MemTotal"),
        "memory_available_bytes": memory.get("MemAvailable"),
        "disk_free_bytes": disk.f_bavail * disk.f_frsize,
        "inodes_free": disk.f_favail,
        "certificates": _observation(Path("/var/lib/mail-admin-certificates/state.json"), now),
        "backup": _observation(Path("/var/lib/mail-admin-backup/manifest.json"), now),
    }


def _observation(path: Path, now: float) -> dict[str, Any]:
    try:
        record = protected_json(path)
        observed = record.get("observed_at", record.get("availability_observed_at", 0))
        if type(observed) not in {int, float}:
            raise ValueError('Invalid host observation timestamp')
        if not 0 <= now - observed <= 300:
            return {"state": "unknown", "reason": "stale"}
        return _public_record(record)
    except (OSError, ValueError, ServiceError):
        return {"state": "unknown", "reason": "unavailable"}


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    result = _scalars(record, OBSERVATION_FIELDS)
    if 'consumers' in record:
        consumers = record['consumers']
        if not isinstance(consumers, dict):
            raise ValueError('Invalid host consumer observations')
        result['consumers'] = {name: _scalars(consumers[name], CONSUMER_FIELDS)
                               for name in ('maddy', 'mail-admin') if name in consumers}
    return result


def _scalars(record: dict[str, Any], fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError('Invalid host observation fields')
    result = {key: value for key, value in record.items() if key in fields}
    if any(type(value) not in {str, int, float, bool, type(None)} for value in result.values()):
        raise ValueError('Unexpected nested host observation')
    return result
