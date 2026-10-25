"""Hold a real partial cutover and a drained legacy timer for an external guest power cut."""

import argparse
import os
import signal
from pathlib import Path
from unittest.mock import patch

from host.docker import Docker
from host.release_files import file_digest
from shared.files import atomic_json, protected_json
from tests.cutover_systemd_support import CutoverSystemdFixture
from tests.host_support import GuestHelper
from tests.kernel_scenario_support import boot_id, pause_at_boundary
from tests.legacy_systemd_support import LegacySystemdFixture, PrivateLegacy
from tools.qualification_bundle import QualificationBundle
from tools.run_tests import source_digest, timestamp


def pause_installation(channel, operation):
    replace = operation.package.files.replace
    calls = []

    def replaced(path, content):
        replace(path, content)
        calls.append(str(path))
        if len(calls) == 2:
            channel.send(('cutover_installation', 2))
            signal.pause()
            raise RuntimeError('Interrupted cutover unexpectedly continued')

    with patch.object(operation.package.files, 'replace', side_effect=replaced):
        operation.run()


def prepare(directory, evidence):
    host = GuestHelper.load()
    identity = {'scope': 'development; not acceptance', 'source_sha256': source_digest()}
    if evidence is not None:
        bundle = QualificationBundle(evidence)
        bundle.verify_source()
        identity.update(bundle.identity, scope='frozen-disposable-cutover-reboot',
                        qualification_suite_sha256=file_digest(
                            bundle.artifacts['qualification_suite']))
    before = LegacySystemdFixture(host.record['instance_id'])
    before.provision()
    before.guard.enter({'manual_renewal_inhibited': True})
    configuration_identity = {name: identity.get(name, source_digest()) for name in (
        'artifact_set_sha256', 'specification_sha256', 'qualification_suite_sha256',
    )}
    fixture = CutoverSystemdFixture(host.record['instance_id'], configuration_identity)
    fixture.prepare_consumers(host.record['runtime_image_id'])
    fixture.legacy.guard.enter({'manual_renewal_inhibited': True})
    operation = fixture.operation()
    with operation.coordination.consistent():
        release = operation.host.snapshot()
    images = tuple(sorted({item['image_id'] for item in release['consumers'].values()}))
    archive = fixture.root / 'unchanged-images.tar'
    Docker(timeout=30).run(('image', 'save', '--output', str(archive)) + images)
    archive.chmod(0o600)
    original = {str(path): (file_digest(path), path.stat().st_mode & 0o777)
                for path in operation.package.files.targets}
    child = pause_at_boundary(pause_installation, operation, ('cutover_installation', 2))
    atomic_json(directory / 'prepared.json', {
        **identity, 'instance_id': host.record['instance_id'], 'boot_id': boot_id(),
        'runtime_image_id': host.record['runtime_image_id'], 'started_at_utc': timestamp(),
        'before_root': str(before.root), 'during_root': str(fixture.root),
        'outcome': str(operation.outcome), 'package': str(operation.package.directory),
        'plan': operation.package.files.plan, 'original_files': original,
        'configuration_identity': configuration_identity,
        'paused_boundary': ['cutover_installation', 2], 'paused_process': child.pid,
    })
    print('Actual legacy timer pause and partial cutover are ready for guest power cut', flush=True)
    while child.is_alive():
        signal.pause()
    raise RuntimeError('The cutover writer exited before the external power cut')


def load_legacy(root, instance):
    if (root.parent != Path('/root/mail-admin-fixture') or root.resolve() != root
            or not root.name.startswith('mail-admin-legacy-')):
        raise RuntimeError('Unexpected private legacy fixture path')
    fixture = LegacySystemdFixture.__new__(LegacySystemdFixture)
    fixture.root, fixture.instance, fixture.identifier = root, instance, root.name
    fixture.units = Path('/etc/systemd/system')
    fixture.guard = PrivateLegacy(fixture)
    return fixture


def restore_fixture(record):
    fixture = CutoverSystemdFixture.__new__(CutoverSystemdFixture)
    fixture.root = Path(record['during_root'])
    fixture.legacy = load_legacy(fixture.root, record['instance_id'])
    fixture.identity = record['configuration_identity']
    fixture.record = protected_json(fixture.root / 'tls-context.json')
    fixture.trust = os.environ.get('SSL_CERT_FILE'), os.environ.get('SSL_CERT_DIR')
    os.environ.update(SSL_CERT_FILE=fixture.record['trust'],
                      SSL_CERT_DIR='/nonexistent-fixture-ca')
    operation = fixture.operation()
    if operation.package.files.plan != record['plan']:
        raise RuntimeError('Rebooted cutover differs from its frozen matched file plan')
    operation.outcome = Path(record['outcome'])
    operation.package.directory = Path(record['package'])
    return fixture, operation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--evidence', type=Path)
    options = parser.parse_args()
    if (options.directory.parent != Path('/root/mail-admin-fixture')
            or not options.directory.name.startswith('cutover-reboot-')
            or options.directory.resolve() != options.directory):
        raise RuntimeError('A private cutover reboot directory is required')
    options.directory.mkdir(mode=0o700, exist_ok=True)
    if (options.directory / 'prepared.json').exists():
        raise RuntimeError('An interrupted cutover record cannot be overwritten')
    prepare(options.directory, options.evidence)


if __name__ == '__main__':
    main()
