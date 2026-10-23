"""Fresh-target fixture installation; a running mail/dashboard consumer always blocks it."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from host.backup import FULL_STATE_SOURCES
from shared.files import protected_json
from tests.host_support import require_fixture_guest

SYSTEM_TARGETS = {
    'maddy': '/opt/maddy/data', 'dashboard': '/opt/mail-admin/data',
    'operator': '/etc/mail-admin', 'helper': '/var/lib/mail-admin-helper',
    'certificates': '/var/lib/mail-admin-certificates', 'host_tools': '/usr/local/lib/mail-admin',
    'tmpfiles': '/etc/tmpfiles.d/mail-admin.conf',
    'docker_ordering': '/etc/systemd/system/docker.service.d/mail-admin-ordering.conf',
    'launcher': '/usr/local/libexec/mail-admin-maddy', 'alerts': '/var/lib/mail-admin-alerts',
    'maddy_configuration': '/opt/maddy/maddy.conf',
    'maddy_compose': '/opt/maddy/full-recovery-compose.yml', 'tls': '/opt/maddy/tls',
    'renewal_script': '/opt/maddy/renew-cert.sh',
}
ARTIFACT_NAMES = {'matched-maddy-dashboard-images.tar', 'host-bundle.tar.gz'}


def command(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=180)
    return result.stdout.strip()


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy_owned(source, target):
    if target.exists() or target.is_symlink() or target.parent.resolve() != target.parent:
        raise RuntimeError('Fresh recovery requires an absent target and trusted parent')
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)
    retain_owners(source, target)


def retain_owners(source, target):
    metadata = source.lstat()
    os.chown(target, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
    if source.is_dir():
        for path in source.rglob('*'):
            metadata = path.lstat()
            os.chown(target / path.relative_to(source), metadata.st_uid, metadata.st_gid,
                     follow_symlinks=False)


class RecoveryInstallation:
    def __init__(self, root, identity):
        self.root, self.identity = root, identity

    def require_new_target(self):
        instance, source = self.identity
        require_fixture_guest(instance)
        if instance == source:
            raise RuntimeError('A different disposable recovery guest is required')
        record = protected_json(self.root.with_name(self.root.name + '.restore.json'))
        if record['state'] != 'credentials_recovered_requires_activation':
            raise RuntimeError('Private-terminal recovery must finish before installation')
        self.verify_utilities(record)

    def verify_utilities(self, record):
        names = {record['utility_name'], record['recovery_utility_name']}
        runtime = protected_json(self.root / 'operator/deployment.json')['dashboard_image_id']
        identifiers = command(('docker', 'ps', '-aq')).splitlines()
        observed = [json.loads(command(('docker', 'inspect', value)))[0] for value in identifiers]
        if {value['Name'].removeprefix('/') for value in observed} != names:
            raise RuntimeError('Only retained authentication utilities may precede installation')
        for value in observed:
            name = value['Name'].removeprefix('/')
            if (value['State']['Running'] or value['State']['Status'] != 'exited'
                or value['State']['ExitCode'] != 0 or value['Image'] != runtime
                or value['Config']['Labels'].get('mail-admin.recovery-run') != name):
                raise RuntimeError('A restore utility has not completed with the exact saved image')

    def targets(self):
        profile = protected_json(self.root / 'operator/deployment.json')
        private = Path(profile['backup_restic']['staging_directory']).parent
        if (private.parent != Path('/root/mail-admin-fixture') or private.is_symlink()
            or not private.name.startswith('full-host-recovery-')):
            raise RuntimeError('Invalid private fixture recovery directory')
        result = {name: Path(path) for name, path in SYSTEM_TARGETS.items()}
        result.update({name: private / relative for name, relative in (
            ('acme', 'acme'), ('acme_lib', 'acme-lib'), ('release_artifacts', 'release-artifacts'),
        )})
        if set(result) | {'units'} != FULL_STATE_SOURCES:
            raise RuntimeError('Every full-state role must have a reviewed target')
        return result

    def verify_release_images(self):
        directory = self.root / 'release_artifacts'
        manifest = protected_json(directory / 'manifest.json')
        if set(manifest['sha256']) != ARTIFACT_NAMES:
            raise RuntimeError('Unexpected saved release artifact set')
        for name, expected in manifest['sha256'].items():
            if file_digest(directory / name) != expected:
                raise RuntimeError('Saved release artifact changed')
        command(('docker', 'load', '-i', str(directory / 'matched-maddy-dashboard-images.tar')))
        for key in ('dashboard_image_id', 'maddy_image_id'):
            actual = json.loads(command(('docker', 'image', 'inspect', manifest[key])))[0]
            self.verify_image(actual, (key, manifest[key]))

    @staticmethod
    def verify_image(actual, expected):
        role, identity = expected
        if (actual['Id'] != identity or actual['Architecture'] != 'amd64'
            or actual['Os'] != 'linux'):
            raise RuntimeError('Saved image identity/architecture mismatch')
        if role == 'dashboard_image_id' and actual['Config']['User'] != '10001:10001':
            raise RuntimeError('The restored web image must use the dedicated non-root identity')

    def verify_bootstrap(self, target):
        for source in (self.root / 'host_tools').rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                actual = target / source.relative_to(self.root / 'host_tools')
                if not actual.is_file() or file_digest(source) != file_digest(actual):
                    raise RuntimeError('Bootstrap host library differs from restored source')

    def install_units(self):
        for source in (self.root / 'units').iterdir():
            if (not source.is_file() or source.is_symlink()
                or source.suffix not in {'.service', '.timer'}
                or not source.name.startswith(('mail-admin-', 'maddy-cert-'))):
                raise RuntimeError('Unexpected captured fixture unit')
            copy_owned(source, Path('/etc/systemd/system') / source.name)

    def install(self):
        self.require_new_target()
        targets = self.targets()
        self.verify_bootstrap(targets['host_tools'])
        for source, target in targets.items():
            if source != 'host_tools':
                copy_owned(self.root / source, target)
        self.install_units()
        copy_owned(self.root / 'host_tools/docker-compose.yml',
                   Path('/opt/mail-admin/docker-compose.yml'))
        command(('systemctl', 'daemon-reload'))
        command(('systemctl', 'enable', 'mail-admin-paths.service', 'mail-admin-helper.service'))
        command(('systemctl', 'start', 'mail-admin-paths.service'))
        return {'roles_installed': 18, 'application_started': False,
                'bootstrap_source_matches': True}
