"""Render the shipped Compose file with only private fixture identities and bind paths."""

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from host.docker import Docker
from tests.tls_compose_support import close_private_compose
from tools.resources import WEB_MEMORY_MIB


@contextmanager
def displaced_source(path):
    identifier = str(uuid.uuid4())
    retained = path.with_name(path.name + '.original-' + identifier)
    directory = path.is_dir()
    path.rename(retained)
    try:
        yield directory
    finally:
        if path.exists() or path.is_symlink():
            path.rename(path.with_name(path.name + '.fault-' + identifier))
        retained.rename(path)


class RuntimeCompose:
    def __init__(self, fixture):
        self.fixture = fixture
        self.project = 'mail-admin-binds-' + str(uuid.uuid4())
        self.container = self.project + '-web'
        self.path = fixture.root / 'runtime-compose.json'
        self.docker = Docker('docker', timeout=30)

    def environment_file(self):
        values = {'IMAGE': os.environ['MAIL_ADMIN_RUNTIME_REFERENCE'], 'UID': '10001',
                  'GID': '10001', 'MEMORY_LIMIT': str(WEB_MEMORY_MIB) + 'm',
                  'TLS_DIRECTORY': self.fixture.daemon_path(self.fixture.tls),
                  'TMPFS_LIMIT': '8388608', 'HASH_MEMORY_KIB': '8192',
                  'HASH_PARALLELISM': '1', 'HTTP_CONCURRENCY': '4',
                  'OPERATION_TIMEOUT_SECONDS': '5'}
        path = self.fixture.root / 'compose-fixture.env'
        path.write_text(''.join('MAIL_ADMIN_' + name + '=' + value + '\n'
                                for name, value in values.items()))
        return path

    def prepare(self):
        self.fixture.mount_root = self.fixture.root
        root = Path(__file__).resolve().parents[1]
        result = self.fixture.command(('compose', '--env-file', str(self.environment_file()),
                                      '-f', str(root / 'docker-compose.yml'),
                                      'config', '--format', 'json'))
        configuration = json.loads(result.stdout)
        service = configuration['services']['mail-admin']
        service.update({'container_name': self.container, 'restart': 'no',
                        'labels': {'mail-admin.fixture': self.project},
                        'ports': ['127.0.0.1::8443'], 'memswap_limit': service['mem_limit'],
                        'cpus': 0.5, 'pids_limit': 128})
        self.private_sources(service['volumes'])
        configuration.update({'name': self.project,
                              'networks': {'default': {'name': self.project + '-default'}}})
        self.path.write_text(json.dumps(configuration))

    def private_sources(self, volumes):
        sources = {target: self.fixture.daemon_path(source)
                   for source, target in self.fixture.bind_sources()}
        sources['/tls'] = self.fixture.daemon_path(self.fixture.tls)
        observed = set()
        for volume in volumes:
            if volume['type'] != 'bind':
                continue
            if volume.get('bind', {}).get('create_host_path') is not False:
                raise RuntimeError('The shipped Compose must refuse missing bind sources')
            volume['source'] = sources[volume['target']]
            observed.add(volume['target'])
        if observed != sources.keys():
            raise RuntimeError('Unexpected runtime Compose bind source set')

    def start(self):
        try:
            self.fixture.command(('compose', '-f', str(self.path), 'create', '--pull',
                                  'never', '--no-build', 'mail-admin'))
        finally:
            self.observe_container()
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.ready()

    def observe_container(self):
        identifiers = self.docker.run(('ps', '-aq', '--filter',
                                       'label=mail-admin.fixture=' + self.project)).splitlines()
        if not identifiers:
            return
        if len(identifiers) != 1:
            raise RuntimeError('Only one private runtime container is allowed')
        record = self.docker.inspect(identifiers[0])
        if record['Name'] != '/' + self.container:
            raise RuntimeError('Unexpected private runtime Compose container')
        self.fixture.identifier = record['Id']

    def close(self):
        close_private_compose(self.docker, self.project, (self.container,))
        self.fixture.identifier = ''
