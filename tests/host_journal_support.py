"""Exercise shipped journal limits in an independent namespace in the owned guest."""

import hashlib
import json
import uuid
from pathlib import Path

from tests.host_support import GuestHelper

JOURNAL_LIMIT_BYTES = 32 * 1024**2
JOURNAL_FILE_ALLOWANCE_BYTES = 8 * 1024**2
PRODUCER = """import base64,os
print('EXPIRED-FIRST-JOURNAL-FIXTURE-MARKER',flush=True)
for _ in range(4096):
 print(base64.b64encode(os.urandom(12288)).decode(),flush=True)
print('CURRENT-LAST-JOURNAL-FIXTURE-MARKER',flush=True)
"""


class PrivateJournal:
    def __init__(self, host: GuestHelper) -> None:
        self.host = host
        self.namespace = 'mail-admin-retention-' + str(uuid.uuid4())
        self.unit = self.namespace + '-writer.service'
        self.service = 'systemd-journald@' + self.namespace + '.service'
        self.configuration = Path('/etc/systemd/journald@' + self.namespace + '.conf')

    def prepare(self) -> None:
        source = Path(__file__).resolve().parents[1] / 'host/systemd/journald-mail-admin.conf'
        with self.configuration.open('xb') as output:
            output.write(source.read_bytes())
        dropin = Path('/run/systemd/system') / (self.service + '.d')
        dropin.mkdir(mode=0o755)
        (dropin / 'fixture-resources.conf').write_text(
            '[Service]\nMemoryMax=96M\nMemorySwapMax=0\nCPUQuota=50%\nTasksMax=32\n'
        )
        self.host.command(('systemctl', 'daemon-reload'))

    def generate(self) -> None:
        self.host.command((
            'systemd-run', '--wait', '--unit', self.unit,
            '--property=LogNamespace=' + self.namespace,
            '--property=MemoryMax=64M', '--property=MemorySwapMax=0',
            '--property=CPUQuota=50%', '--property=TasksMax=16',
            '--property=RuntimeMaxSec=120', '--property=StandardOutput=journal',
            '/usr/bin/python3', '-c', PRODUCER,
        ))
        self.host.command(('journalctl', '--namespace=' + self.namespace, '--sync'))

    def observe(self) -> dict:
        markers = self.host.command((
            'journalctl', '--namespace=' + self.namespace, '--no-pager', '--output=cat',
            '--unit=' + self.unit, '--grep=JOURNAL-FIXTURE-MARKER', '--lines=16',
        ))
        machine = Path('/etc/machine-id').read_text().strip()
        directory = Path('/var/log/journal') / (machine + '.' + self.namespace)
        journals = list(directory.glob('*.journal'))
        if not journals:
            raise RuntimeError('The private journal did not create persistent storage')
        return {'scope': 'Shipped journal capacity retention in a private guest namespace',
                'namespace': self.namespace, 'writer_unit': self.unit,
                'configuration_sha256': hashlib.sha256(self.configuration.read_bytes()).hexdigest(),
                'old_marker_retained': 'EXPIRED-FIRST-JOURNAL-FIXTURE-MARKER' in markers,
                'new_marker_retained': 'CURRENT-LAST-JOURNAL-FIXTURE-MARKER' in markers,
                'allocated_bytes': sum(path.stat().st_blocks * 512 for path in journals),
                'journal_files': len(journals)}

    def close(self) -> None:
        self.host.command((
            'systemctl', 'stop', self.service,
            'systemd-journald@' + self.namespace + '.socket',
            'systemd-journald-varlink@' + self.namespace + '.socket',
        ))


def retain_observation(journal: PrivateJournal, observation: dict) -> None:
    destination = Path('build') / (journal.namespace + '.json')
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    with destination.open('x') as output:
        json.dump(observation, output, indent=2)
        output.write('\n')
