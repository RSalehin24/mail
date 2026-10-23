"""Reproduce a failed dependency start with private systemd units, without touching Docker."""

import configparser
import subprocess
import time
import uuid
from pathlib import Path

from shared.files import atomic_json
from tests.host_support import require_fixture_guest


def command(arguments):
    return subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=60)


class DependencyFixture:
    def __init__(self, instance, source):
        require_fixture_guest(instance)
        configuration = configparser.ConfigParser(interpolation=None)
        configuration.read_string(source.read_text())
        self.wanted = configuration['Install']['WantedBy'].split()
        if not set(self.wanted) <= {'multi-user.target', 'docker.service'}:
            raise RuntimeError('Unexpected dependency fixture installation targets')
        self.identifier = 'mail-admin-dependency-' + str(uuid.uuid4())
        self.root = Path('/root/mail-admin-fixture') / self.identifier
        self.root.mkdir(mode=0o700)
        self.units = Path('/etc/systemd/system')

    def name(self, role):
        suffix = '.target' if role == 'boot' else '.service'
        return self.identifier + '-' + role + suffix

    def write(self, role, content):
        with (self.units / self.name(role)).open('x') as stream:
            stream.write(content)

    def provision(self):
        self.write('docker', '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                   + 'ExecStart=/usr/bin/test -e ' + str(self.root / 'ready') + '\n')
        wanted = [self.name('boot' if name == 'multi-user.target' else 'docker')
                  for name in self.wanted]
        self.write('helper', '[Unit]\nRequires=' + self.name('docker') + '\nAfter='
                   + self.name('docker') + '\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                   + 'ExecStart=/usr/bin/touch ' + str(self.root / 'helper-started')
                   + '\n[Install]\nWantedBy=' + ' '.join(wanted) + '\n')
        self.write('boot', '[Unit]\nDescription=Private dependency recovery fixture\n')
        command(('systemctl', 'daemon-reload'))
        command(('systemctl', 'enable', self.name('helper')))

    def run(self):
        self.provision()
        try:
            self.require_failed_dependency()
            resumed = self.recover_dependency()
            atomic_json(self.root / 'observation.json', {
                'result': 'pass' if resumed else 'fail', 'actual_initial_dependency_failure': True,
                'helper_resumed_after_only_dependency_started': resumed,
                'source_wanted_by': self.wanted, 'real_docker_modified': False,
            })
            if not resumed:
                raise RuntimeError('Helper stayed inactive after its dependency recovered')
        finally:
            command(('systemctl', 'stop', self.name('helper'),
                     self.name('docker'), self.name('boot')))
        return self.root

    def require_failed_dependency(self):
        command(('systemctl', 'start', self.name('boot')))
        if (self.root / 'helper-started').exists():
            raise RuntimeError('The failed dependency unexpectedly started its client')
        result = command(('systemctl', 'show', self.name('docker'), '-p', 'Result', '--value'))
        if result.stdout.strip() != 'exit-code':
            raise RuntimeError('The dependency must really fail before testing recovery')

    def recover_dependency(self):
        (self.root / 'ready').touch(mode=0o600)
        command(('systemctl', 'reset-failed', self.name('docker')))
        command(('systemctl', 'start', self.name('docker')))
        deadline = time.monotonic() + 15
        while not (self.root / 'helper-started').is_file() and time.monotonic() < deadline:
            time.sleep(0.1)
        return (self.root / 'helper-started').is_file()
