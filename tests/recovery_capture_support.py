"""Capture and verify an immutable full-state recovery point through the approved real R2 repo."""

import argparse
import smtplib
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from helper.maddy import DockerMaddy
from host.backup import FULL_STATE_SOURCES, BackupPaths, CaptureDependencies, SnapshotCapture
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.docker import Docker, DockerSnapshots
from host.locks import LockPolicy
from host.repository_backup import RepositoryBackup
from host.restic_process import ResticProcess
from host.restic_repository import ResticRepository
from host.snapshot_tree import snapshot_tree
from shared.files import atomic_json, protected_json
from tests.host_support import GuestHelper
from tests.recovery_activation_support import verify_mail
from tools.qualification_bundle import QualificationBundle
from tools.qualification_host import verify_host_installation
from tools.run_tests import source_digest, timestamp


class VerifiedRepository(ResticRepository):
    def verify(self, publication):
        super().verify(publication)
        self.verified_at_ns = time.time_ns()


class ObservedManifest:
    def __init__(self, path, repository):
        self.path, self.repository = path, repository
        self.published_at_ns = None

    def __call__(self, path, record):
        verified = getattr(self.repository, 'verified_at_ns', None)
        if path != self.path or verified is None or self.published_at_ns is not None:
            raise RuntimeError('Trusted manifest write must follow repository verification once')
        atomic_json(path, record)
        self.published_at_ns = time.time_ns()
        if self.published_at_ns < verified:
            raise RuntimeError('Publication observation clock preceded verification')


class ObservedCapture(SnapshotCapture):
    def _copy(self, snapshot):
        self.require_stopped()
        super()._copy(snapshot)
        self.require_stopped()

    def require_stopped(self):
        docker = self.dependencies.services.docker
        if any(docker.inspect(name)['State']['Running'] for name in ('maddy', 'mail-admin')):
            raise RuntimeError('Both actual writers must remain stopped throughout full capture')


def deliver(body):
    content = ('From: fixture@rsalehin24.me\r\nTo: helper-lifecycle@rsalehin24.me\r\n'
               'Subject: Immutable full-state recovery fixture\r\n\r\n' + body + '\r\n')
    with smtplib.SMTP('127.0.0.1', 25, timeout=5) as connection:
        refused = connection.sendmail('fixture@rsalehin24.me',
                                       ['helper-lifecycle@rsalehin24.me'], content)
        if refused:
            raise RuntimeError('The actual source rejected its recovery fixture message')


def require_context(options):
    root = Path(options['root'])
    if (root.parent != Path('/root/mail-admin-fixture') or root.resolve() != root
            or not root.name.startswith('full-host-recovery-')):
        raise RuntimeError('A private full-state recovery fixture is required')
    if (root / 'source-capture.json').exists():
        raise RuntimeError('Existing source recovery evidence cannot be overwritten')
    host = GuestHelper.load()
    bundle = QualificationBundle(Path(options['evidence']))
    bundle.verify_source()
    verify_host_installation(bundle, host)
    return root, host, bundle


def source_dependencies(profile, host):
    inputs = profile['inputs']
    generations = Generations(GenerationPaths(Path('/opt/maddy/tls/generations'),
        Path('/var/lib/mail-admin-certificates/state.json')),
        (CertificatePolicy(inputs['service_gid']), PairValidator(), time.time))
    locks = MaintenanceLocks(Path('/run/lock/mail-admin-renewal.lock'),
        Path('/run/lock/mail-admin-maddy.lock'), LockPolicy(inputs['lock_wait_seconds']))
    coordination = Coordination(locks, (host.journal, generations))
    docker = Docker(timeout=inputs['operation_timeout_seconds'])
    services = DockerSnapshots((docker, DockerMaddy(profile), generations))
    return CaptureDependencies(coordination, services, time.time)


def capture(options):
    root, host, bundle = require_context(options)
    profile = protected_json(Path('/etc/mail-admin/deployment.json'))
    sources = {name: Path(path) for name, path in profile['backup_sources'].items()}
    if set(sources) != FULL_STATE_SOURCES:
        raise RuntimeError('Every full-state source must be real and explicitly selected')
    repository = VerifiedRepository(ResticProcess(profile), time.time)
    repository.verify_identity()
    dependencies = source_dependencies(profile, host)
    paths = BackupPaths(root / 'staging', root / 'manifest.json', sources,
                         tuple(profile['maddy_message_paths']))
    operation = RepositoryBackup(ObservedCapture(paths, dependencies), repository)
    started = timestamp()
    deliver(options['message_observation']['captured_body'])
    verify_mail(options['message_observation'])
    snapshot = operation.capture()
    before = snapshot_tree(snapshot).sha256
    deliver(options['message_observation']['post_capture_body'])
    verify_mail({'captured_body': options['message_observation']['post_capture_body']})
    if snapshot_tree(snapshot).sha256 != before or paths.manifest.exists():
        raise RuntimeError('Live mail changed immutable staging or a manifest was published early')
    publication = ObservedManifest(paths.manifest, repository)
    with patch('host.repository_backup.atomic_json', publication):
        manifest = operation.transfer(snapshot)
    after = snapshot_tree(snapshot).sha256
    if after != before:
        raise RuntimeError('Off-server transfer changed immutable staging')
    if publication.published_at_ns is None:
        raise RuntimeError('A trusted manifest preceded actual repository download verification')
    endpoint = urlsplit(profile['backup_r2']['endpoint'])
    if endpoint.scheme != 'https' or not endpoint.hostname.endswith('.r2.cloudflarestorage.com'):
        raise RuntimeError('Full-state fixture copying must use the approved off-VM R2 endpoint')
    atomic_json(root / 'source-capture.json', {
        **bundle.identity, 'source_sha256': source_digest(), 'result': 'pass',
        'scope': 'frozen-disposable-source-capture',
        'source_instance_id': host.record['instance_id'], 'role_count': len(sources),
        'reference': manifest['reference'], 'manifest': manifest, 'snapshot': str(snapshot),
        'content_before_live_delivery': before, 'content_after_transfer': after,
        'verified_at_ns': repository.verified_at_ns,
        'manifest_published_at_ns': publication.published_at_ns,
        'manifest_mtime_ns': paths.manifest.stat().st_mtime_ns,
        'started_at_utc': started, 'completed_at_utc': timestamp(),
        'message_observation': options['message_observation'], 'r2_host': endpoint.hostname,
    })


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('context', type=Path)
    capture(protected_json(parser.parse_args().context))
