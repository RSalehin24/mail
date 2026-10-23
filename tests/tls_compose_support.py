"""Private Compose services exercise the actual DockerConsumer recreation command."""

import json
import os
import uuid
from pathlib import Path

from host.docker import Docker
from tools.resources import MADDY_MEMORY_MIB, WEB_MEMORY_MIB


def bind(source, target):
    return {'type': 'bind', 'source': str(source), 'target': target,
            'read_only': target != '/data' and target != '/app/data',
            'bind': {'create_host_path': False}}


def close_private_compose(docker, project, names):
    identifiers = docker.run(('ps', '-aq', '--filter',
                              'label=mail-admin.fixture=' + project)).splitlines()
    for identifier in identifiers:
        record = docker.inspect(identifier)
        if record['Name'].removeprefix('/') not in names:
            raise RuntimeError('Unexpected private Compose fixture container')
        docker.run(('rm', '--force', identifier))
    networks = docker.run(('network', 'ls', '-q', '--filter',
                           'label=com.docker.compose.project=' + project))
    for network in networks.splitlines():
        docker.run(('network', 'rm', network))


class ConsumerCompose:
    def __init__(self, mail, web):
        self.mail, self.web = mail, web
        self.project = 'mail-admin-tls-' + str(uuid.uuid4())
        self.path = mail.root / 'consumers.json'
        self.names = {'maddy': mail.container, 'mail-admin': self.project + '-web'}
        self.services = {}
        self.docker = Docker(mail.docker, timeout=30)

    def configuration(self):
        maddy = self.base('maddy', self.mail.maddy_image, MADDY_MEMORY_MIB)
        maddy.update({'entrypoint': '/usr/local/libexec/mail-admin-maddy',
                      'command': ['-config', '/data/maddy.conf', 'run'],
                      'ports': [f'127.0.0.1:{self.mail.imap_port}:993',
                                f'127.0.0.1:{self.mail.smtp_port}:25'],
                      'tmpfs': ['/run/maddy:size=1048576,mode=0700'], 'volumes': [
                          bind(self.mail.daemon_path(self.mail.data), '/data'),
                          bind(self.mail.daemon_path(self.mail.configuration), '/data/maddy.conf'),
                          bind(self.mail.daemon_path(self.mail.root / 'maddy-launcher'),
                               '/usr/local/libexec/mail-admin-maddy'),
                          bind('${TLS_DIRECTORY}', '/data/tls'),
                      ]})
        web = self.base('mail-admin', os.environ['MAIL_ADMIN_RUNTIME_REFERENCE'], WEB_MEMORY_MIB)
        web.update({'user': '10001:10001', 'read_only': True, 'cap_drop': ['ALL'],
                    'security_opt': ['no-new-privileges:true'], 'ports': ['127.0.0.1::8443'],
                    'tmpfs': ['/tmp:size=8388608,mode=1777'],  # noqa: S108 -- private tmpfs.
                    'environment': {'HASH_MEMORY_KIB': '8192', 'HASH_PARALLELISM': '1',
                                    'HTTP_CONCURRENCY': '4', 'OPERATION_TIMEOUT_SECONDS': '5'},
                    'volumes': [bind(self.web.daemon_path(source), target)
                                for source, target in self.web.bind_sources()]
                    + [bind('${TLS_DIRECTORY}', '/tls')]})
        self.services = {'maddy': maddy, 'mail-admin': web}
        self.write()

    def base(self, role, image, memory):
        return {'image': image, 'container_name': self.names[role], 'platform': 'linux/amd64',
                'pull_policy': 'never', 'restart': 'no', 'mem_limit': str(memory) + 'm',
                'memswap_limit': str(memory) + 'm', 'cpus': 0.5, 'pids_limit': 128,
                'labels': {'mail-admin.fixture': self.project},
                'logging': {'driver': 'local', 'options': {'max-size': '1m', 'max-file': '2'}}}

    def write(self):
        self.path.write_text(json.dumps({'name': self.project, 'services': self.services}))

    def up(self, role, generation):
        self.docker.run(('compose', '-f', str(self.path), 'up', '-d', '--no-build',
                         '--pull', 'never', '--no-deps', '--force-recreate', role),
                        {'TLS_DIRECTORY': self.mail.daemon_path(generation)})

    def record(self, role):
        actual = self.docker.inspect(self.names[role])
        target = '/data/tls' if role == 'maddy' else '/tls'
        port = '993/tcp' if role == 'maddy' else '8443/tcp'
        return {'container': self.names[role], 'image_id': actual['Image'],
                'compose_file': str(self.path), 'service': role,
                'tls_target': target, 'tls_variable': 'TLS_DIRECTORY', 'environment': {},
                'tls_port': int(actual['NetworkSettings']['Ports'][port][0]['HostPort'])}

    def pin_ports(self, role):
        actual = self.docker.inspect(self.names[role])
        self.services[role]['ports'] = [
            '127.0.0.1:' + bindings[0]['HostPort'] + ':' + target.removesuffix('/tcp')
            for target, bindings in actual['NetworkSettings']['Ports'].items() if bindings
        ]
        self.write()

    def close(self):
        close_private_compose(self.docker, self.project, tuple(self.names.values()))


class ConsumerFixtureDocker:
    def __init__(self, compose):
        self.compose = compose
        self.timeout = 15

    def inspect(self, name):
        if name not in self.compose.names.values():
            raise AssertionError('Refusing a consumer outside the private Compose project')
        actual = self.compose.docker.inspect(name)
        daemon_root = Path(self.compose.mail.daemon_path(self.compose.mail.root))
        for mount in actual['Mounts']:
            source = Path(mount['Source'])
            if source.is_relative_to(daemon_root):
                mount['Source'] = str(self.compose.mail.root / source.relative_to(daemon_root))
        return actual

    def run(self, arguments, environment):
        if arguments[:3] != ('compose', '-f', str(self.compose.path)):
            raise AssertionError('Refusing Compose outside the private fixture')
        generation = Path(environment['TLS_DIRECTORY'])
        if not generation.is_relative_to(self.compose.mail.root):
            raise AssertionError('Refusing a certificate outside the private fixture')
        return self.compose.docker.run(arguments, {
            **environment, 'TLS_DIRECTORY': self.compose.mail.daemon_path(generation),
        })
