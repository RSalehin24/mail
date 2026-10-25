"""Observe cgroup memory, which includes charged tmpfs and child processes."""

import json
import time


def container_memory(fixture, identifier):
    script = ('cat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.max '
              '/sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.stat')
    lines = fixture.command(('exec', identifier, '/bin/sh', '-c', script)).stdout.splitlines()
    counters = dict(line.split() for line in lines[2:])
    state = json.loads(fixture.command(('inspect', identifier)).stdout)[0]
    return {'peak_bytes': int(lines[0]), 'limit_bytes': int(lines[1]),
            'tmpfs_charged_bytes': int(counters['shmem']),
            'oom_kill': int(counters['oom_kill']), 'image': state['Image'],
            'container_id': state['Id'], 'running': state['State']['Running']}


def wait_for_probe(fixture, identifier):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = json.loads(fixture.command(('inspect', identifier)).stdout)[0]['State']
        if not state['Running']:
            raise RuntimeError('The isolated resource probe exited before observation')
        if 'resource-probe-ready' in fixture.command(('logs', identifier)).stdout:
            return
        time.sleep(0.1)
    raise RuntimeError('The isolated resource probe missed its fifteen-second bound')
