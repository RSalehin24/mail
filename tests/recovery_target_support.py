"""Execute recovery on a different, empty disposable guest using an external kit."""

import argparse
import re
import secrets
import time
import uuid
from datetime import datetime
from pathlib import Path

from host.backup_cli import prepare_state
from host.docker import Docker
from host.image_tools import ImageTools
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from host.restore import Restore
from shared.files import atomic_json, protected_json
from tests.host_support import require_fixture_guest
from tests.recovery_install_support import RecoveryInstallation, command
from tests.recovery_terminal import TerminalRecovery
from tools.qualification_bundle import QualificationBundle
from tools.run_tests import source_digest, timestamp


class RecoveryTarget:
    def __init__(self, context):
        self.context = context
        require_fixture_guest(context['target_instance_id'])
        source = context['source_instance_id']
        if str(uuid.UUID(source)) != source or source == context['target_instance_id']:
            raise RuntimeError('Recovery requires a different disposable source guest')
        self.root = Path(context['directory'])
        if (self.root.parent != Path('/root/mail-admin-fixture')
            or self.root.name != 'replacement-' + context['target_instance_id']
            or self.root.resolve() != self.root):
            raise RuntimeError('Recovery requires its own private fixture directory')
        self.destination = self.root / 'restored'

    def require_empty(self):
        if command(('docker', 'ps', '-aq')) or command(('docker', 'image', 'ls', '-q')):
            raise RuntimeError('The replacement must start without containers or images')
        if any(Path(path).exists() for path in (
            '/etc/mail-admin', '/opt/maddy', '/opt/mail-admin',
        )):
            raise RuntimeError('Existing application state blocks fresh recovery')
        if not Path('/usr/bin/docker').is_file():
            raise RuntimeError('The host tools require Docker at /usr/bin/docker before restore')

    def operation(self):
        profile = protected_json(self.root / 'profile.json')
        prepare_state(profile)
        return Restore(ResticRepository(ResticProcess(profile), time.time))

    def installation(self):
        return RecoveryInstallation(self.destination, (
            self.context['target_instance_id'], self.context['source_instance_id'],
        ))

    def tools(self):
        profile = protected_json(self.destination / 'operator/deployment.json')
        return ImageTools(Docker(), profile)

    def record(self, phase, facts):
        if re.fullmatch(r'[a-z][a-z-]{0,48}', phase) is None:
            raise RuntimeError('Unexpected recovery observation phase')
        path = self.root / (phase + '-observation.json')
        if path.exists():
            raise RuntimeError('Existing recovery evidence must be preserved; use a fresh attempt')
        stamp = timestamp()
        atomic_json(path, {
            **facts, **self.qualification_binding(),
            'phase': phase, 'observed_at': datetime.fromisoformat(stamp).timestamp(),
            'observed_at_utc': stamp,
            'target_instance_id': self.context['target_instance_id'],
            'source_instance_id': self.context['source_instance_id'],
        })

    def qualification_binding(self):
        evidence = self.context.get('qualification_evidence')
        if evidence is None:
            return {'scope': 'development; not acceptance'}
        bundle = QualificationBundle(Path(evidence))
        bundle.verify_source()
        return {**bundle.identity, 'source_sha256': source_digest(),
                'scope': 'frozen-disposable-replacement-recovery'}

    def stage(self):
        self.require_empty()
        self.record('admission', {'empty_target': True, 'services_started': False})
        started = time.monotonic()
        staged = self.operation().stage(self.context['reference'], self.destination)
        if staged['state'] != 'authentication_closed' or not staged['full_state_sources_present']:
            raise RuntimeError('Full-state recovery must remain authentication closed')
        self.record('stage', {**staged, 'elapsed_seconds': time.monotonic() - started})
        self.installation().verify_release_images()
        prepared = self.operation().prepare_authentication(self.destination, self.tools())
        if prepared['state'] != 'prepared_requires_ssh_recovery':
            raise RuntimeError('Authentication preparation must require private recovery')
        self.record('prepare', {**prepared, 'services_started': False})

    def recover(self):
        password = secrets.token_urlsafe(40)
        material = TerminalRecovery(self.operation(), self.tools()).recover(
            self.destination, password,
        )
        atomic_json(self.root / 'replacement-auth.json', {
            'seed': material.seed, 'codes': list(material.codes), 'password': password,
        })
        self.record('recover', {'private_terminal_completed': True,
                                'credentials_logged': False, 'services_started': False})

    def install(self):
        result = self.installation().install()
        self.record('install', result)

    def activate(self):
        from tests.recovery_activation_support import RecoveryActivation

        RecoveryActivation(self).activate()

    def verify(self):
        from tests.recovery_activation_support import RecoveryActivation

        RecoveryActivation(self).verify()

    def boot(self):
        from tests.recovery_boot_support import RestoredBoot

        RestoredBoot(self).verify()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('context', type=Path)
    parser.add_argument('phase',
                        choices=('stage', 'recover', 'install', 'activate', 'verify', 'boot'))
    arguments = parser.parse_args()
    target = RecoveryTarget(protected_json(arguments.context))
    getattr(target, arguments.phase)()


if __name__ == '__main__':
    main()
