"""Activate only installed synthetic state, then verify its real helper, mail and HTTPS."""

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from helper.journal import Journal
from helper.maddy import DockerMaddy
from host.container_policy import ContainerPolicy
from host.docker import Docker
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RuntimePolicy
from tests.fixture_values import LIFECYCLE_MAILBOX_PASSWORD
from tests.fixture_values import REPLACEMENT_PASSWORD as LEGACY_FIXTURE_PASSWORD
from tests.host_tls_support import LoopbackImap, recovery_context
from tests.recovery_install_support import command
from tests.recovery_web_support import (
    RecoveredHttps,
    restored_permissions,
    verify_closed_authentication,
)

MAILBOX = 'helper-lifecycle@rsalehin24.me'
BODY = b'full-host-recovery-body-512f66c5'


def credential_digest():
    with closing(sqlite3.connect('file:/opt/maddy/data/credentials.db?mode=ro', uri=True)) as db:
        records = sorted(db.execute('SELECT * FROM passwords').fetchall())
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def verify_mail(expected=None):
    with LoopbackImap(recovery_context(), 5) as connection:
        connection.login(MAILBOX, LIFECYCLE_MAILBOX_PASSWORD)
        if connection.select('INBOX')[0] != 'OK':
            raise RuntimeError('Restored fixture inbox unavailable')
        status, identifiers = connection.search(None, 'ALL')
        if status != 'OK' or not identifiers[0]:
            raise RuntimeError('Restored fixture messages missing')
        bodies = []
        for identifier in identifiers[0].split():
            status, content = connection.fetch(identifier, '(RFC822)')
            if status != 'OK' or not isinstance(content[0], tuple):
                raise RuntimeError('Restored fixture body unavailable')
            bodies.append(content[0][1])
        content = b'\n'.join(bodies)
        if BODY not in content:
            raise RuntimeError('Restored fixture body changed')
        if expected is not None:
            excluded = expected.get('post_capture_body')
            if (expected['captured_body'].encode() not in content
                    or excluded is not None and excluded.encode() in content):
                raise RuntimeError('Restored mail does not match the immutable source cutoff')
    return {'mailbox_imap_authenticated': True, 'original_message_body_restored': True,
            'captured_message_visible': expected is not None,
            'post_capture_message_absent': expected is not None
            and expected.get('post_capture_body') is not None}


class RecoveryActivation:
    def __init__(self, target):
        self.target = target
        self.profile = protected_json(Path('/etc/mail-admin/deployment.json'))
        self.docker = Docker(timeout=30)

    def require_installed(self):
        observed = protected_json(self.target.root / 'install-observation.json')
        if (observed['target_instance_id'] != self.target.context['target_instance_id']
            or observed['roles_installed'] != 18 or observed['application_started'] is not False):
            raise RuntimeError('Full-state installation must precede activation')
        self.target.installation().require_new_target()
        if Journal(Path('/var/lib/mail-admin-helper/journal.db')).unresolved():
            raise RuntimeError('Unresolved restored operations block activation')

    def start_maddy(self):
        self.docker.run(('compose', '-f', '/opt/maddy/full-recovery-compose.yml',
                         'up', '-d', '--no-build', '--pull', 'never'))
        maddy = DockerMaddy(self.profile)
        maddy.validate()
        actual = maddy.inventories()
        if actual != {'credentials': [MAILBOX], 'storage': [MAILBOX]}:
            raise RuntimeError('Restored native mailbox inventories changed')
        return actual

    def start_dashboard(self):
        # This isolated profile deliberately reuses its already measured one-off limits.
        policy = ContainerPolicy.load(self.profile)
        environment = {**self.profile['consumers']['mail-admin']['environment'],
                       'MAIL_ADMIN_IMAGE': self.profile['dashboard_image_id'],
                       'MAIL_ADMIN_UID': '10001', 'MAIL_ADMIN_GID': '10001',
                       'MAIL_ADMIN_MEMORY_LIMIT': str(policy.memory),
                       'MAIL_ADMIN_TLS_DIRECTORY': '/opt/maddy/tls/fixture',
                       'MAIL_ADMIN_TMPFS_LIMIT': str(policy.temporary)}
        for name in ('HASH_MEMORY_KIB', 'HASH_PARALLELISM', 'HTTP_CONCURRENCY',
                     'OPERATION_TIMEOUT_SECONDS'):
            environment['MAIL_ADMIN_' + name] = environment[name]
        override = self.target.root / 'bounded-web-override.json'
        atomic_json(override, {'services': {'mail-admin': {
            'memswap_limit': policy.memory, 'cpus': policy.cpus, 'pids_limit': 128,
        }}})
        RuntimePolicy(Path('/etc/mail-admin/installation.json'),
                      Path('/etc/mail-admin/secrets/runtime-policy.json')).resume()
        self.docker.run(('compose', '-f', '/opt/mail-admin/docker-compose.yml',
                         '-f', str(override), 'up', '-d', '--no-build', '--pull', 'never'),
                        environment)
        return RecoveredHttps().ready()

    def activate(self):
        self.require_installed()
        self.target.record('activation-intent', {'application_started': False})
        before = credential_digest()
        old = protected_json(self.target.root / 'original-auth.json')
        checks = verify_closed_authentication(self.target.tools(), old)
        inventory = self.start_maddy()
        command(('systemctl', 'start', 'mail-admin-helper.service'))
        elapsed = self.start_dashboard()
        self.target.record('activate', {**checks, 'inventories': inventory,
            'https_readiness_seconds': elapsed, 'credential_digest_before': before,
            'application_started': True, 'boot_id': Path('/proc/sys/kernel/random/boot_id')
            .read_text().strip()})

    def verify(self):
        activated = protected_json(self.target.root / 'activate-observation.json')
        if not activated['application_started']:
            raise RuntimeError('Verified activation is required')
        timeout = float(self.profile['consumers']['mail-admin']['environment']
                        ['OPERATION_TIMEOUT_SECONDS'])
        material = protected_json(self.target.root / 'replacement-auth.json')
        web = RecoveredHttps(timeout + 5, material.get('password', LEGACY_FIXTURE_PASSWORD))
        old = protected_json(self.target.root / 'original-auth.json')
        checks = web.old_authentication_rejected(old)
        cookie = web.login(material)
        atomic_json(self.target.root / 'verified-cookie.json', {'cookie': cookie})
        self.target.record('https-authentication', {
            **checks, 'replacement_password_and_totp_authenticated': True,
        })
        status, _, inventory = web.api('GET', '/api/accounts')
        if status != 200 or [row['email'] for row in inventory] != [MAILBOX]:
            raise RuntimeError('Authenticated HTTPS to actual helper inventory failed')
        if credential_digest() != activated['credential_digest_before']:
            raise RuntimeError('Restored credentials changed during activation')
        mail = verify_mail(self.target.context.get('message_observation'))
        self.target.record('verify', {**checks, **mail, **restored_permissions(),
            'https_nonroot_helper_inventory_passed': True, 'credentials_unchanged': True,
            'observed_at': time.time(), 'boot_id': Path('/proc/sys/kernel/random/boot_id')
            .read_text().strip()})
