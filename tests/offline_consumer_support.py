"""The real private-terminal operator utility with only fixture bind sources substituted."""

import os
from pathlib import Path

from host.docker import Docker
from host.image_tools import ImageTools
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from tests.fixture_values import PASSWORD
from tests.recovery_terminal import TerminalRecovery
from tests.snapshot_consumer_support import SnapshotConsumerDocker


class FixtureImageTools(ImageTools):
    def __init__(self, scene):
        self.web = scene.services.web
        name = scene.services.records['mail-admin']['container']
        deployment = {**self.web.operator_deployment(name),
                      'dashboard_image_id': os.environ['MAIL_ADMIN_RUNTIME_IMAGE']}
        super().__init__(Docker('/usr/bin/docker'), deployment)

    def authentication_arguments(self, action, destination=None):
        if destination is not None:
            raise RuntimeError('Only the exact isolated runtime may be recovered')
        sources = tuple((Path(self.web.daemon_path(source)), target) for source, target in (
            (self.web.root / 'data', '/app/data'),
            (self.web.root / 'keys', '/run/secrets/mail-admin'),
            (self.web.root / 'installation.json', '/run/mail-admin-installation.json'),
        ))
        return self._authentication_arguments(action, sources)


class NamedRecovery:
    def __init__(self, tools, name):
        self.tools, self.name = tools, name

    def recover_authentication(self, _destination, _tools):
        self.tools.offline_utility(self.name)


class OfflineFixtureConsumer:
    def __init__(self, scene):
        self.scene, self.web = scene, scene.services.web
        self.tools = FixtureImageTools(scene)
        self.policy = RuntimePolicy(self.web.root / 'installation.json',
                                    self.web.root / 'keys' / RUNTIME_POLICY_NAME, os.getuid())
        self.material = None

    def pause_runtime(self):
        self.policy.pause()

    def stop(self):
        SnapshotConsumerDocker(self.scene.services).stop_for_maintenance('mail-admin')

    def snapshot_authentication(self):
        self.web.database.snapshot(self.web.root / 'before-offline-recovery.db')

    def offline_utility(self, name):
        action = NamedRecovery(self.tools, name)
        self.material = TerminalRecovery(action, self.tools).recover(None, PASSWORD)

    def discard_recovery(self, name):
        self.tools.discard_recovery(name)

    def verify_recovery(self, name):
        self.tools.verify_recovery(name)

    def allow_runtime(self):
        self.policy.resume()

    def verify_running(self):
        expected = self.scene.generations.read()['served_sha256']
        if self.scene.services.fingerprints()['mail-admin'] != expected:
            raise RuntimeError('Recovered fixture did not serve its verified complete pair')
