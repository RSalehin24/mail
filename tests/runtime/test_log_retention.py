import json
import re
import subprocess
import threading
import unittest
from pathlib import Path

from tests.runtime_compose_support import RuntimeCompose
from tests.runtime_support import RuntimeFixture

LOG_READ_TIMEOUT_SECONDS = 30


def log_size_bytes(value):
    match = re.fullmatch(r'([1-9][0-9]*)m', value)
    if match is None:
        raise ValueError('The shipped log-size limit must be expressed in MiB')
    return int(match[1]) * 1024**2


def observed_output(identifier):
    expired, current, total = False, False, 0
    with subprocess.Popen(('docker', 'logs', identifier), stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        timer = threading.Timer(LOG_READ_TIMEOUT_SECONDS, process.kill)
        timer.start()
        try:
            for line in process.stdout:
                total += len(line)
                expired = expired or b'EXPIRED-FIRST-FIXTURE-MARKER' in line
                current = current or b'CURRENT-LAST-FIXTURE-MARKER' in line
            code = process.wait(timeout=5)
        finally:
            timer.cancel()
    if code:
        raise RuntimeError('The isolated log read did not complete successfully')
    return {'old_marker_retained': expired, 'new_marker_retained': current,
            'retained_output_bytes': total}


class ContainerLogRetentionTests(unittest.TestCase):
    def test_shipped_local_driver_rotates_old_output_and_retains_current_logs(self):
        fixture = RuntimeFixture()
        self.addCleanup(fixture.close)
        compose = RuntimeCompose(fixture)
        self.addCleanup(compose.close)
        compose.prepare()
        configuration = json.loads(compose.path.read_bytes())
        service = configuration['services']['mail-admin']
        logging = service['logging']
        self.assertEqual(logging['driver'], 'local')
        options = logging['options']
        limit, copies = log_size_bytes(options['max-size']), int(options['max-file'])
        self.assertGreater(copies, 0)
        self.assertLessEqual(limit * (copies + 2), 64 * 1024**2)
        program = """import sys
print('EXPIRED-FIRST-FIXTURE-MARKER',flush=True)
line='x'*16383+'\\n'
for _ in range(RECORDS): sys.stdout.write(line)
print('CURRENT-LAST-FIXTURE-MARKER',flush=True)
""".replace('RECORDS', str(limit * (copies + 1) // 16384))
        service.update(command=['python', '-c', program], network_mode='none', ports=[])
        service.pop('networks', None)
        compose.path.write_text(json.dumps(configuration))
        fixture.command(('compose', '-f', str(compose.path), 'create', '--pull',
                         'never', '--no-build', 'mail-admin'))
        compose.observe_container()
        fixture.command(('start', fixture.identifier))
        self.assertEqual(compose.docker.run(('wait', fixture.identifier)), '0')
        state = compose.docker.inspect(fixture.identifier)
        self.assertFalse(state['State']['OOMKilled'])
        self.assertEqual(state['HostConfig']['LogConfig'],
                         {'Type': logging['driver'], 'Config': options})
        observed = observed_output(fixture.identifier)
        self.assertFalse(observed['old_marker_retained'])
        self.assertTrue(observed['new_marker_retained'])
        self.assertLessEqual(observed['retained_output_bytes'], limit * copies + 32768)
        record = {'scope': 'Actual shipped Docker log rotation; not host journal retention',
                  'container_id': state['Id'], 'image_id': state['Image'],
                  'logging': logging, **observed}
        (Path('build') / (compose.project + '-log-retention.json')).write_text(
            json.dumps(record, indent=2) + '\n')
