"""Confine the production cutover adapter to private units, files, TLS and containers."""

import hashlib
import os
import time
from pathlib import Path
from types import SimpleNamespace

from host.cutover import Cutover
from host.cutover_host import RootCutoverHost, Systemd
from host.docker import Docker, DockerConsumer
from host.release_files import ReleaseFiles, RollbackPackage, file_digest
from host.renewal import Certbot, Renewal
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.kernel_scenario_support import generation_host
from tests.legacy_systemd_support import LegacySystemdFixture


class PrivateSystemd(Systemd):
    def __init__(self, fixture):
        super().__init__(30)
        self.fixture = fixture

    def run(self, arguments):
        translated = tuple(self.fixture.unit(value.rsplit('.', 1)[-1])
                           if value in {'maddy-cert-renew.service', 'maddy-cert-renew.timer'}
                           else value for value in arguments)
        return super().run(translated)


class CutoverDocker(Docker):
    def __init__(self, record, archive):
        super().__init__(timeout=30)
        self.names = record['names']
        self.archive = archive
        self.images = tuple(sorted({item['image_id'] for item in record['records'].values()}))

    def inspect(self, name):
        return super().inspect(self.names.get(name, name))

    def run(self, arguments, environment=None):
        if arguments[:3] == ('image', 'save', '--output'):
            if arguments[4:] != self.images:
                raise RuntimeError('Cutover image archive differs from the private consumers')
            if self.archive.exists():
                os.link(self.archive, arguments[3])
                return ''
            result = super().run(arguments, environment)
            os.link(arguments[3], self.archive)
            return result
        return super().run(arguments, environment)


class PrivateCutoverHost(RootCutoverHost):
    def _validate_plan(self):
        if set(self.files.plan) != {'renewal_service', 'renewal_timer', 'renewal_script',
                                    'maddy_compose'}:
            raise RuntimeError('The private cutover requires its complete matched file set')
        for record in self.files.plan.values():
            if not Path(record['source']).is_relative_to(self.files.targets[2].parent):
                raise RuntimeError('Private cutover candidate escaped its fixture')

    def _loaded_service(self):
        expected = self.files.plan['renewal_service']
        observation = self.systemd.show('maddy-cert-renew.service',
                                        'FragmentPath,DropInPaths,ExecStart')
        if (observation.get('FragmentPath') != expected['target']
                or observation.get('DropInPaths') != ''
                or self.configuration['service_command'] not in observation.get('ExecStart', '')
                or file_digest(Path(expected['target'])) != expected['sha256']):
            raise ServiceError('loaded_renewal_service_mismatch', 409)
        self._loaded_timer()

    def verify_service(self):
        super().verify_service()
        atomic_json(self.files.targets[2].parent / 'served-verified.json',
                    {'observed_at': time.time()})


class CutoverSystemdFixture:
    def __init__(self, instance, identity):
        self.legacy = LegacySystemdFixture(instance)
        self.legacy.provision()
        self.legacy.release()
        self.root, self.identity = self.legacy.root, identity
        self.record = None

    def prepare_consumers(self, runtime):
        root = self.root
        (root / 'fixtures').mkdir(mode=0o700)
        source = Path(__file__).resolve().parents[1]
        arguments = ('run', '--rm', '--user', '0:0', '--memory', '192m', '--memory-swap', '192m',
                     '--cpus', '0.5', '--pids-limit', '128', '--network', 'host', '--read-only',
                     '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--cap-add', 'FOWNER',
                     '--cap-add', 'DAC_OVERRIDE', '--security-opt', 'no-new-privileges:true',
                     '--tmpfs', '/tmp:size=8388608,mode=1777')  # noqa: S108 -- bounded utility.
        for path, target in ((source, Path('/app')), (root, root),
                             (root / 'fixtures', Path('/fixtures')),
                             (Path('/var/run/docker.sock'), Path('/var/run/docker.sock')),
                             (Path('/usr/bin/docker'), Path('/usr/bin/docker')),
                             (Path('/usr/local/libexec/docker/cli-plugins'),
                              Path('/usr/local/libexec/docker/cli-plugins')),
                             (Path('/var/lib/cloud/data/instance-id'),
                              Path('/var/lib/cloud/data/instance-id'))):
            writable = {root, root / 'fixtures', Path('/var/run/docker.sock')}
            readonly = '' if path in writable else ',readonly'
            arguments += ('--mount', f'type=bind,src={path},dst={target}{readonly}')
        for value in ('MAIL_ADMIN_FIXTURE_ROOT=/fixtures',
                      'MAIL_ADMIN_FIXTURE_DAEMON_ROOT=' + str(root / 'fixtures'),
                      'TMPDIR=/fixtures', 'MAIL_ADMIN_RUNTIME_IMAGE=' + runtime,
                      'MAIL_ADMIN_RUNTIME_REFERENCE=' + runtime):
            arguments += ('-e', value)
        Docker(timeout=180).run(arguments + (runtime, 'python', '-m',
                               'tests.prepare_cutover_fixture', str(root), self.legacy.instance))
        self.record = protected_json(root / 'tls-context.json')
        self.trust = os.environ.get('SSL_CERT_FILE'), os.environ.get('SSL_CERT_DIR')
        os.environ.update(SSL_CERT_FILE=self.record['trust'],
                          SSL_CERT_DIR='/nonexistent-fixture-ca')

    def operation(self):
        record = self.record
        scene = generation_host(Path(record['mail_root']) / 'renewal', Path(record['trust']))
        docker = CutoverDocker(record, self.root / 'unchanged-images.tar')
        files = self.release_files()
        deployment = {**self.identity, 'cutover': {'support_files': [str(Path(__file__))],
                                  'service_command': self.service_command()},
                      'schema_version': 1,
                      'helper_protocol': 1, 'renewal_timeout_seconds': 30,
                      'inputs': {'operation_timeout_seconds': 5}, 'consumers': record['records']}
        context = SimpleNamespace(deployment=deployment, docker=docker,
                                   generations=scene.generations, coordination=scene.coordination)
        context.renewal = lambda: Renewal(scene.coordination,
            (Certbot(docker, (scene.generations, deployment)), {
                role: DockerConsumer(consumer, docker)
                for role, consumer in record['records'].items()
            }))
        host = PrivateCutoverHost(context, files)
        host.systemd, host.legacy = PrivateSystemd(self.legacy), self.legacy.guard
        outcome = self.root / ('cutover-' + str(time.time_ns()) + '.json')
        outcome.with_suffix('.lock').touch(mode=0o600)
        package = RollbackPackage(outcome.with_suffix('.package'), files, docker)
        return Cutover(scene.coordination, (host, package, outcome))

    def service_command(self):
        return ('/usr/bin/python3 -m tests.cutover_service_worker '
                + str(self.root / 'tls-context.json'))

    def release_files(self):
        root = self.root
        script = root / 'renew-cert.sh'
        if not script.exists():
            script.write_text('#!/bin/sh\nexit 0\n')
            script.chmod(0o750)
        targets = (self.legacy.units / self.legacy.unit('service'),
                   self.legacy.units / self.legacy.unit('timer'), script,
                   Path(self.record['mail_root']) / 'consumers.json')
        service = ('[Service]\nType=oneshot\nWorkingDirectory='
                   + str(Path(__file__).resolve().parents[1]) + '\nExecStart='
                   + self.service_command() + '\nTimeoutStartSec=30s\nUMask=0077\n')
        candidates = (service.encode(), targets[1].read_bytes(),
                      b'#!/bin/sh\nexec /usr/bin/python3 -m host.cli renew\n',
                      targets[3].read_bytes())
        names = ('renewal_service', 'renewal_timer', 'renewal_script', 'maddy_compose')
        plan = {}
        for name, target, value in zip(names, targets, candidates, strict=True):
            source = root / (name + '.candidate')
            source.write_bytes(value)
            source.chmod(0o600)
            plan[name] = {'target': str(target), 'source': str(source),
                          'sha256': hashlib.sha256(value).hexdigest(),
                          'mode': 0o750 if name == 'renewal_script' else 0o644}
        return ReleaseFiles(plan, (0, targets))

    def close(self):
        from tests.tls_compose_support import close_private_compose

        self.legacy.close()
        if self.record is not None:
            close_private_compose(Docker(), self.record['project'],
                                   tuple(self.record['names'].values()))
            for key, value in zip(('SSL_CERT_FILE', 'SSL_CERT_DIR'), self.trust, strict=True):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
