"""Only fixed allowlisted host observations; missing/stale state is explicitly unknown."""

import os
import time
from pathlib import Path
from typing import Any

from shared.errors import ServiceError
from shared.files import protected_json

OBSERVATION_FIELDS = frozenset({
    'observed_at', 'outcome', 'cutoff', 'verified', 'available', 'restore_passed', 'expiry',
    'desired_generation', 'availability_observed_at', 'served_sha256', 'enabled', 'provider',
    'renewal_observed_at',
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
        **_cpu_sampler.read(),
        "memory_total_bytes": memory.get("MemTotal"),
        "memory_available_bytes": memory.get("MemAvailable"),
        "disk_free_bytes": disk.f_bavail * disk.f_frsize,
        "disk_total_bytes": disk.f_blocks * disk.f_frsize,
        "disk_used_bytes": (disk.f_blocks - disk.f_bfree) * disk.f_frsize,
        "disk_reserved_bytes": max(0, disk.f_bfree - disk.f_bavail) * disk.f_frsize,
        "inodes_free": disk.f_favail,
        "thresholds": _thresholds(),
        "certificates": _certificates(now),
        "monitoring": _observation(
            Path("/var/lib/mail-admin-alerts/monitor-observation.json"), now, 600,
        ),
        "external_monitoring": _observation(
            Path("/var/lib/mail-admin-alerts/external-monitoring.json"), now, 86400,
        ),
        "backup": _observation(Path("/var/lib/mail-admin-backup/manifest.json"), now),
    }


def _observation(path: Path, now: float, max_age: float = 300) -> dict[str, Any]:
    try:
        record = protected_json(path)
        observed = record.get("observed_at", record.get("availability_observed_at", 0))
        if type(observed) not in {int, float}:
            raise ValueError('Invalid host observation timestamp')
        public = _public_record(record)
        if observed <= 0 or observed > now:
            return {"state": "unknown", "reason": "invalid_observation_time"}
        if now - observed > max_age:
            return {**public, "state": "stale", "reason": "stale"}
        return public
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


def _thresholds() -> dict[str, Any]:
    try:
        inputs = protected_json(Path("/etc/mail-admin/deployment.json"))["inputs"]
        fields = ("disk_free_minimum_bytes", "inode_free_minimum", "recovery_point_seconds")
        values = {field: inputs[field] for field in fields}
        if any(type(value) is not int or value < 0 for value in values.values()):
            raise ValueError("Invalid monitoring thresholds")
        return values
    except (OSError, ValueError, KeyError, TypeError, ServiceError):
        return {"state": "unavailable"}


def _certificates(now: float) -> dict[str, Any]:
    renewal = _observation(Path("/var/lib/mail-admin-certificates/state.json"), now)
    served = _observation(Path("/var/lib/mail-admin-alerts/served-certificates.json"), now, 600)
    if "consumers" in served:
        return {**renewal, **served, "outcome": renewal.get("outcome", "unavailable"),
                "state": served.get("state", "fresh"), "reason": served.get("reason"),
                "renewal_observed_at": renewal.get("observed_at")}
    return renewal


def _cpu_ticks() -> dict[str, int]:
    try:
        lines = Path('/proc/stat').read_text().splitlines()
        values = [int(value) for value in lines[0].split()[1:9]]
        if lines[0].split()[0] != 'cpu' or len(values) != 8 or min(values) < 0:
            return {}
        return {'cpu_total_ticks': sum(values), 'cpu_idle_ticks': values[3] + values[4],
                'cpu_logical_count': sum(line.split()[0][3:].isdigit()
                                         for line in lines if line.startswith('cpu'))}
    except (OSError, ValueError, IndexError):
        return {}


class CpuSampler:
    """Share host deltas across clients; subsecond reads reuse the original sample."""

    def __init__(self) -> None:
        import threading
        self.lock = threading.Lock()
        self.previous: tuple[float, dict[str, int]] | None = None
        self.sample: dict[str, Any] = {}

    def read(self) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            if self.previous is not None and now - self.previous[0] < 1:
                return dict(self.sample)
            ticks = _cpu_ticks()
            previous = self.previous
            self.previous = (now, ticks) if ticks else None
            self.sample = dict(ticks)
            if previous is not None and ticks:
                interval = now - previous[0]
                total = ticks['cpu_total_ticks'] - previous[1]['cpu_total_ticks']
                idle = ticks['cpu_idle_ticks'] - previous[1]['cpu_idle_ticks']
                if (0 < interval <= 3 and total > 0 and 0 <= idle <= total
                        and ticks['cpu_logical_count'] == previous[1]['cpu_logical_count']):
                    self.sample.update({
                        'cpu_utilization_percent': 100 * (total - idle) / total,
                        'cpu_sample_interval_seconds': interval,
                        'cpu_sampled_at': time.time(),
                    })
            return dict(self.sample)


_cpu_sampler = CpuSampler()
