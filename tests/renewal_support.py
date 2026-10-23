"""Real durable generation, journal and lock state for isolated renewal tests."""

import os
import time

from helper.journal import Journal
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import LockPolicy
from host.renewal import Certbot, Renewal
from shared.files import atomic_json
from tests.certbot_host_support import PrivateCertbotDocker
from tools.fixture_images import CERTBOT_IMAGE


class PrivateRenewal:
    def __init__(self, client):
        root = client.fixture.root / 'renewal'
        root.mkdir(mode=0o700)
        (root / 'generations').mkdir(mode=0o750)
        for name in ('renewal.lock', 'maddy.lock'):
            (root / name).touch(mode=0o600)
        atomic_json(root / 'state.json', {'coordination_migrated': True, 'consumers': {
            name: {'intent': 'never_deployed', 'state': 'absent'}
            for name in ('maddy', 'mail-admin')
        }})
        self.generations = Generations(
            GenerationPaths(root / 'generations', root / 'state.json'),
            (CertificatePolicy(10001, os.getuid()),
             PairValidator(client.network.root / 'issuance-root.pem'), time.time),
        )
        self.coordination = Coordination(MaintenanceLocks(
            root / 'renewal.lock', root / 'maddy.lock', LockPolicy(1, os.getuid())),
            (Journal.provision(root / 'journal.db'), self.generations))
        self.docker = PrivateCertbotDocker(client)
        self.certbot = Certbot(self.docker, (self.generations, {
            'certbot_image': CERTBOT_IMAGE, 'transient_resources': {
                'memory_limit_bytes': 128 * 1024**2,
                'tmpfs_limit_bytes': 8 * 1024**2, 'cpu_limit': 0.5,
            },
        }))
        self.renewal = Renewal(self.coordination, (self.certbot, {}))
