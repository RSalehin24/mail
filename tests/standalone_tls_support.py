"""Real TLS consumers and shared locks without an unnecessary ACME acquisition."""

import os
import time
from unittest.mock import patch

from helper.journal import Journal
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import LockPolicy
from host.renewal import Renewal
from shared.files import atomic_json
from tests.maddy_support import RealMaddyFixture
from tests.tls_consumer_support import PrivateTlsConsumers


class StandaloneTls:
    def __init__(self):
        self.mail = RealMaddyFixture()
        root = self.mail.root / 'renewal'
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
            (CertificatePolicy(10001, os.getuid()), PairValidator(self.mail.certificates.trust),
             time.time),
        )
        self.coordination = Coordination(MaintenanceLocks(
            root / 'renewal.lock', root / 'maddy.lock', LockPolicy(1, os.getuid()),
        ), (Journal.provision(root / 'journal.db'), self.generations))
        self.renewal = Renewal(self.coordination, (None, {}))
        self.services = PrivateTlsConsumers(self.mail, self)
        self.trust_boundary = patch.dict(os.environ, {
            'SSL_CERT_FILE': str(self.mail.certificates.trust),
            'SSL_CERT_DIR': str(self.mail.root / 'no-system-ca'),
        })
        self.trust_boundary.start()

    def start(self):
        return self.services.start()

    def close(self):
        try:
            self.services.close()
        finally:
            try:
                self.mail.close()
            finally:
                self.trust_boundary.stop()
