"""Exercise the real legacy guard against private units in the owned mock guest."""

import os
import subprocess
import time
import uuid
from pathlib import Path

from host.legacy import LegacyRenewal
from shared.files import atomic_json
from tests.host_support import require_fixture_guest


def systemctl(arguments):
    result = subprocess.run(('/usr/bin/systemctl',) + arguments, capture_output=True,
                            text=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError('Private systemd fixture command failed: ' + result.stderr[-2000:])
    return result.stdout.strip()


class PrivateLegacy(LegacyRenewal):
    def __init__(self, fixture):
        self.fixture = fixture
        super().__init__(fixture.root / 'legacy.json', 30)

    def _systemctl(self, arguments):
        translated = tuple(self.fixture.unit(value.rsplit('.', 1)[-1])
                           if value in {'maddy-cert-renew.service', 'maddy-cert-renew.timer'}
                           else value for value in arguments)
        return systemctl(translated)

    def _managed_legacy(self, entry):
        try:
            values = (entry / 'cgroup').read_bytes().splitlines()
        except FileNotFoundError:
            return False
        expected = ('/system.slice/' + self.fixture.unit('service')).encode()
        paths = [line.rsplit(b':', 1)[-1] for line in values]
        return any(path == expected or path.startswith(expected + b'/') for path in paths)


class LegacySystemdFixture:
    def __init__(self, instance):
        require_fixture_guest(instance)
        self.instance = instance
        self.identifier = 'mail-admin-legacy-' + str(uuid.uuid4())
        self.root = Path('/root/mail-admin-fixture') / self.identifier
        self.root.mkdir(mode=0o700)
        self.units = Path('/etc/systemd/system')
        self.guard = PrivateLegacy(self)

    def unit(self, suffix):
        if suffix not in {'service', 'timer'}:
            raise RuntimeError('Unexpected private legacy unit role')
        return self.identifier + '.' + suffix

    def writer_arguments(self):
        source = Path(__file__).with_name('legacy_writer_worker.py')
        return ('/usr/bin/python3', str(source), str(self.root), self.instance,
                '/opt/maddy/renew-cert.sh')

    def provision(self):
        # This argument is observed by the real /proc writer detector; the worker never runs it.
        service = ('[Service]\nType=oneshot\nExecStart=' + ' '.join(self.writer_arguments())
                   + '\nTimeoutStartSec=30s\nUMask=0077\n')
        timer = ('[Timer]\nOnCalendar=*-*-* 03:00:00 UTC\nPersistent=true\n'
                 '[Install]\nWantedBy=timers.target\n')
        for suffix, value in (('service', service), ('timer', timer)):
            with (self.units / self.unit(suffix)).open('x') as stream:
                stream.write(value)
        stamp = Path('/var/lib/systemd/timers') / ('stamp-' + self.unit('timer'))
        stamp.parent.mkdir(exist_ok=True)
        stamp.touch(mode=0o600)
        systemctl(('daemon-reload',))
        systemctl(('enable', '--now', self.unit('timer')))

    def start_writer(self):
        systemctl(('start', '--no-block', self.unit('service')))
        self.wait_for('writer-started.json')

    def wait_for(self, name):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if (self.root / name).is_file():
                return
            time.sleep(0.1)
        raise RuntimeError('Private legacy fixture missed its fifteen-second observation bound')

    def release(self):
        (self.root / 'release').touch(mode=0o600)

    def observe(self):
        return {suffix: systemctl(('show', self.unit(suffix),
                                   '--property=ActiveState,UnitFileState,Result,ExecMainStatus'))
                for suffix in ('service', 'timer')}

    def save(self, facts):
        atomic_json(self.root / 'observation.json', {**facts, 'instance_id': self.instance,
                    'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    'actual_units': self.observe(), 'observed_at': time.time()})

    def close(self):
        self.release()
        systemctl(('disable', '--now', self.unit('timer')))
        systemctl(('stop', self.unit('service')))
        # Preserve unit contents and results with the fixture; only remove its loaded names.
        for suffix in ('service', 'timer'):
            path = self.units / self.unit(suffix)
            if path.is_file() and not path.is_symlink():
                os.rename(path, self.root / path.name)
        systemctl(('daemon-reload',))
