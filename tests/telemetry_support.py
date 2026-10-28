"""Explicit synthetic chart data for isolated browser fixtures; never production telemetry."""
import time
from datetime import UTC, datetime


def synthetic_resources():
    now = time.time()
    return {
        'observed_at': now, 'scope': 'synthetic browser fixture',
        'cpu_total_ticks': int(now * 100), 'cpu_idle_ticks': int(now * 63),
        'cpu_logical_count': 1, 'cpu_utilization_percent': 37,
        'cpu_sample_interval_seconds': 1, 'cpu_sampled_at': now,
        'memory_total_bytes': 1024**3, 'memory_available_bytes': 512*1024**2,
        'disk_total_bytes': 32*1024**3, 'disk_free_bytes': 16*1024**3,
        'disk_used_bytes': 15*1024**3, 'disk_reserved_bytes': 1024**3,
        'inodes_free': 1000000,
        'thresholds': {'disk_free_minimum_bytes': 1024**3,
                       'inode_free_minimum': 10000, 'recovery_point_seconds': 93600},
        'certificates': {'observed_at': now, 'outcome': 'active', 'consumers': {
            name: {'state': 'tls_verified', 'served_expiry': now+60*86400}
            for name in ('maddy', 'mail-admin')
        }},
        'backup': {'availability_observed_at': now, 'verified': True, 'available': True,
                   'restore_passed': False,
                   'cutoff': datetime.fromtimestamp(now-1800, UTC).isoformat()},
        'monitoring': {'observed_at': now, 'outcome': 'checks_completed'},
        'external_monitoring': {'observed_at': now, 'enabled': True},
    }
