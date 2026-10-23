"""A new bounded host process reads pending activation without running Certbot."""

import os
import re
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from helper.journal import Journal
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.docker import DockerConsumer
from host.locks import LockPolicy
from host.renewal import Renewal
from shared.files import protected_json
from tests.maddy_support import RealMaddyFixture
from tests.tls_compose_support import ConsumerCompose, ConsumerFixtureDocker


def load_fixture(path):
    context = protected_json(path, os.getuid())
    root = Path(context['mail_root'])
    if not root.is_relative_to(Path(os.environ['MAIL_ADMIN_FIXTURE_ROOT'])):
        raise RuntimeError('Activation context escaped the isolated fixture volume')
    if path.parent != root or re.fullmatch(r'mail-admin-tls-[0-9a-f-]{36}',
                                          context['project']) is None:
        raise RuntimeError('Invalid private activation context')
    mail = RealMaddyFixture.__new__(RealMaddyFixture)
    mail.root, mail.docker = root, shutil.which('docker')
    mail.container = context['names']['maddy']
    compose = ConsumerCompose(mail, SimpleNamespace(root=Path(context['web_root'])))
    compose.project, compose.names = context['project'], context['names']
    validate_records(context, compose)
    return context, compose


def validate_records(context, compose):
    names, records = context['names'], context['records']
    if set(names) != {'maddy', 'mail-admin'} or set(records) - set(names):
        raise RuntimeError('Unexpected private TLS consumer roles')
    if (re.fullmatch(r'mail-admin-fixture-[0-9a-f-]{36}', names['maddy']) is None
            or names['mail-admin'] != compose.project + '-web'):
        raise RuntimeError('Refusing a consumer outside the private TLS fixture')
    for role, record in records.items():
        if (record['container'] != names[role] or record['service'] != role
                or record['compose_file'] != str(compose.path)):
            raise RuntimeError('Private TLS consumer identity changed')


def resume(path):
    context, compose = load_fixture(path)
    root = compose.mail.root / 'renewal'
    generations = Generations(
        GenerationPaths(root / 'generations', root / 'state.json'),
        (CertificatePolicy(10001, os.getuid()), PairValidator(Path(context['trust'])), time.time),
    )
    coordination = Coordination(MaintenanceLocks(
        root / 'renewal.lock', root / 'maddy.lock', LockPolicy(1, os.getuid())),
        (Journal(root / 'journal.db'), generations))
    consumers = {role: DockerConsumer(record, ConsumerFixtureDocker(compose))
                 for role, record in context['records'].items()}
    # retry_activation never invokes its Certbot dependency.
    Renewal(coordination, (None, consumers)).retry_activation()


if __name__ == '__main__':
    resume(Path(sys.argv[1]))
