"""Measure recovery age and elapsed outage without replacing approved objective limits."""

import math


def qualification_objectives(limits, timeline):
    cutoff, outage, verified = timeline
    values = (*timeline, limits['recovery_point_seconds'], limits['recovery_time_seconds'])
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        raise RuntimeError('Recovery objectives require finite observed timestamps and limits')
    if (cutoff > outage or verified < outage or limits['recovery_point_seconds'] <= 0
        or limits['recovery_time_seconds'] <= 0):
        raise RuntimeError('Invalid backup, outage or verification chronology')
    age, elapsed = outage - cutoff, verified - outage
    return {'result': 'pass' if (age <= limits['recovery_point_seconds']
                                and elapsed <= limits['recovery_time_seconds']) else 'fail',
            'backup_age_at_outage_seconds': age, 'outage_to_verified_restore_seconds': elapsed,
            'maximum_backup_age_seconds': limits['recovery_point_seconds'],
            'maximum_restore_elapsed_seconds': limits['recovery_time_seconds'],
            'backup_cutoff': cutoff, 'outage_started_at': outage, 'verified_at': verified}
