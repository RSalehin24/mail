"""Start actual mail and HTTPS consumers with immutable generation mounts."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from host.certificates import served_fingerprint
from host.docker import DockerConsumer
from shared.files import atomic_json
from tests.runtime_support import RuntimeFixture
from tests.tls_compose_support import ConsumerCompose, ConsumerFixtureDocker


class PrivateTlsConsumers:
    def __init__(self, mail, renewal):
        self.mail, self.renewal = mail, renewal
        self.start_mail = mail.start
        self.web = RuntimeFixture()
        self.compose = ConsumerCompose(mail, self.web)
        self.records = {}

    def start(self):
        return self.start_roles(('maddy', 'mail-admin'))

    def start_mail_only(self):
        return self.start_roles(('maddy',))

    def start_roles(self, roles):
        self.start_mail()
        self.web.mount_root = self.web.root
        generation = self.renewal.generations.stage(self.mail.tls)
        self.compose.configuration()
        self.mail.command(('rm', '--force', self.mail.identifier))
        self.mail.identifier = ''
        for role in roles:
            self.deploy(role, generation)
        self.renewal.generations.publish(generation)
        for role in self.records:
            self.renewal.generations.register(role)
        self.renewal.renewal.consumers = self.consumers()
        self.renewal.generations.activate(self.renewal.renewal.consumers)
        return generation

    def deploy(self, role, generation):
        self.compose.up(role, generation)
        self.compose.pin_ports(role)
        self.records[role] = self.compose.record(role)

    def consumers(self):
        docker = ConsumerFixtureDocker(self.compose)
        return {role: DockerConsumer(record, docker) for role, record in self.records.items()}

    def fingerprints(self):
        return {role: served_fingerprint(('127.0.0.1', record['tls_port']))
                for role, record in self.records.items()}

    def close(self):
        try:
            self.compose.close()
        finally:
            self.web.close()

    def combined_trust(self, issuance_root):
        trust = self.mail.root / 'consumer-trust.pem'
        trust.write_bytes(self.mail.certificates.trust.read_bytes() + issuance_root.read_bytes())
        self.renewal.generations.validator.trust_file = trust
        return {'SSL_CERT_FILE': str(trust), 'SSL_CERT_DIR': str(self.mail.root / 'no-system-ca')}

    def observe(self):
        observations = {}
        for role, record in self.records.items():
            container = self.compose.docker.inspect(record['container'])
            observations[role] = {
                'image_id': container['Image'], 'running': container['State']['Running'],
                'tls_mount': next(mount['Source'] for mount in container['Mounts']
                                  if mount['Destination'] == record['tls_target']),
                'served_sha256': served_fingerprint(('127.0.0.1', record['tls_port'])),
                'observed_at': time.time(), 'observer_uid': os.getuid(),
            }
        return observations

    def save_context(self):
        context = self.mail.root / ('activation-context-' + str(uuid.uuid4()) + '.json')
        atomic_json(context, {
            'mail_root': str(self.mail.root), 'web_root': str(self.web.root),
            'project': self.compose.project, 'names': self.compose.names, 'records': self.records,
            'trust': str(self.renewal.generations.validator.trust_file),
        })
        return context

    def resume_in_new_process(self):
        context = self.save_context()
        result = subprocess.run((sys.executable, '-m', 'tests.tls_activation_worker', str(context)),
                                capture_output=True, text=True, timeout=90, check=False)
        if result.returncode:
            raise RuntimeError('Fresh-process activation failed: ' + result.stderr[-4000:])

    def save_observation(self):
        path = Path('build') / ('actual-tls-consumers-' + self.compose.project + '.json')
        atomic_json(path, self.observe())
