"""Prepare real paused TLS/recovery operations, then verify them after an external power cut."""

import argparse
import os
import signal
import time
import unittest
from pathlib import Path

from host.certificates import served_fingerprint, verify_served
from host.recovery import Recovery
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from tests.host_reboot_support import require_reboot
from tests.host_support import require_fixture_guest
from tests.kernel_scenario_support import (
    STAGING_BOUNDARIES,
    activation_child,
    assert_complete_generations,
    boot_id,
    generation_host,
    pause_at_boundary,
    pause_generation,
    provision_generations,
    require_matching_source,
)
from tests.maddy.test_maintenance_coordination import interrupt_recovery
from tests.offline_consumer_support import OfflineFixtureConsumer
from tests.runtime_auth_support import RuntimeAuthentication
from tests.standalone_tls_support import StandaloneTls
from tests.tls_activation_worker import load_fixture, resume
from tests.tls_support import CertificateFixture
from tools.maddy_fixture import fixture_reference
from tools.qualification_bundle import QualificationBundle
from tools.run_tests import controller_memory, source_digest, timestamp


def binding(options):
    require_fixture_guest(options.instance)
    root = options.directory.resolve()
    if root.parent != Path('/root/mail-admin-fixture') or not root.name.startswith('kernel-'):
        raise RuntimeError('An isolated guest scenario directory is required')
    result = {'instance_id': options.instance, 'source_sha256': source_digest(),
              'runtime_image_id': os.environ['MAIL_ADMIN_RUNTIME_IMAGE'],
              'qualification_image_id': os.environ['MAIL_ADMIN_QUALIFICATION_IMAGE'],
              'maddy_fixture_image_id': fixture_reference(),
              'scope': 'development; not acceptance'}
    if options.evidence is not None:
        bundle = QualificationBundle(options.evidence)
        bundle.verify_source()
        result.update(bundle.identity, scope='frozen-disposable-kernel-scenarios')
    return result


def prepare_staging(directory):
    certificates = CertificateFixture(directory)
    original, source = certificates.issue('original'), certificates.issue('next')
    records, processes = [], []
    for index, boundary in enumerate(STAGING_BOUNDARIES):
        root = directory / ('staging-' + str(index))
        host = provision_generations(root, certificates.trust)
        generation = host.generations.stage(original)
        host.generations.publish(generation)
        processes.append(pause_generation(host, source, boundary))
        records.append({'root': str(root), 'source': str(source), 'trust': str(certificates.trust),
                        'old_generation': generation.name, 'boundary': boundary})
    return records, processes


def restart_after_boot(scene):
    for role in scene.services.records:
        scene.services.compose.services[role]['restart'] = 'unless-stopped'
        name = scene.services.records[role]['container']
        scene.services.compose.docker.run(('update', '--restart', 'unless-stopped', name))
    scene.services.compose.write()


def prepare_activation():
    scene = StandaloneTls()
    scene.services.start_mail = scene.mail.launch
    original = scene.services.start_mail_only()
    restart_after_boot(scene)
    generations = scene.generations
    rollback, obsolete, desired = [generations.stage(scene.mail.certificates.issue(role))
                                   for role in ('rollback', 'obsolete', 'desired')]
    state = generations.read()
    state['rollback_generations'] = [rollback.name]
    generations.write(state)
    for path in (original, rollback, obsolete, desired):
        age = time.time() - 8 * 86400
        os.utime(path, (age, age))
    source = scene.mail.root / 'desired'
    context = scene.services.save_context()
    scene.generations.publish(desired)
    child = pause_at_boundary(activation_child, (scene, ('consumer', 'maddy')),
                              ('consumer', 'maddy'))
    return scene, child, {'context': str(context), 'source': str(source),
                          'old_generation': original.name, 'rollback': rollback.name,
                          'obsolete': obsolete.name, 'desired': desired.name,
                          'boundary': ('consumer', 'maddy')}


def prepare_recovery():
    scene = StandaloneTls()
    scene.services.start_mail = scene.mail.launch
    scene.start()
    restart_after_boot(scene)
    web = scene.services.web
    web.port = scene.services.records['mail-admin']['tls_port']
    web.certificates.trust = scene.mail.certificates.trust
    RuntimeAuthentication(unittest.TestCase(), web).enroll()
    outcome = scene.mail.root / 'recovery-outcome.json'
    outcome.with_suffix('.lock').touch(mode=0o600)
    operation = Recovery(scene.coordination, (scene.generations, OfflineFixtureConsumer(scene),
                                             outcome))
    context = scene.services.save_context()
    process = pause_at_boundary(interrupt_recovery, operation, ('offline_utility', None))
    scene.services.compose.docker.run(('stop', scene.services.records['maddy']['container']))
    return scene, process, {'context': str(context), 'outcome': str(outcome),
                            'boundary': ('offline_utility', None)}


def prepare(options):
    record = binding(options)
    directory = options.directory
    directory.mkdir(mode=0o700, exist_ok=True)
    if (directory / 'prepared.json').exists():
        raise RuntimeError('An existing kernel scenario cannot be prepared again')
    record.update(boot_id=boot_id(), started_at_utc=timestamp())
    staging, processes = prepare_staging(directory) if options.only == 'tls' else ([], [])
    scenes = []
    offline = None
    active = None
    selected = []
    if options.only == 'recovery':
        recovery, recovery_process, offline = prepare_recovery()
        scenes.append(recovery)
        processes.append(recovery_process)
        selected.append('T05.05')
    else:
        activation, activation_process, active = prepare_activation()
        scenes.append(activation)
        processes.append(activation_process)
        selected.append('T15.06')
    if staging:
        selected.append('T15.03')
    # Keep live objects and all paused writers until the external controller cuts guest power.
    keep_alive = (scenes, processes)
    record.update(staging=staging, activation=active, recovery=offline,
                  paused_processes=[process.pid for process in processes],
                  selected_cases=selected)
    atomic_json(directory / 'prepared.json', record)
    print('All exact kernel interruption boundaries observed', flush=True)
    while all(process.is_alive() for process in keep_alive[1]):
        signal.pause()
    raise RuntimeError('A paused boundary writer exited before the external power cut')


def verify_staging(record):
    for item in record['staging']:
        host = generation_host(Path(item['root']), Path(item['trust']))
        assert_complete_generations(host)
        source = Path(item['source'])
        with host.coordination.consistent():
            generation = host.generations.stage(source)
            host.generations.publish(generation)
            host.generations.activate({})
        assert_complete_generations(host)
        if host.generations.read()['outcome'] != 'active':
            raise RuntimeError('Kernel-interrupted publication did not recover')


def fixture_host(context):
    root = Path(context['mail_root']) / 'renewal'
    return generation_host(root, Path(context['trust']))


def verify_activation(item):
    path = Path(item['context'])
    context, compose = load_fixture(path)
    host = fixture_host(context)
    os.environ.update(SSL_CERT_FILE=context['trust'], SSL_CERT_DIR='/nonexistent-fixture-ca')
    assert_complete_generations(host)
    expected = host.generations.validator.validate(Path(item['source']))['served_sha256']
    old = host.generations.validator.validate(host.generations.paths.directory
                                               / item['old_generation'])['served_sha256']
    for consumer in context['records'].values():
        fingerprints = {old, expected}
        if served_fingerprint(('127.0.0.1', consumer['tls_port'])) not in fingerprints:
            raise RuntimeError('Rebooted consumer served an incomplete or foreign generation')
    with host.coordination.consistent():
        host.generations.prune(7 * 86400)
    directory = host.generations.paths.directory
    if (directory / item['obsolete']).exists() or any(
        not (directory / item[key]).is_dir() for key in ('old_generation', 'rollback', 'desired')
    ):
        raise RuntimeError('Retention lost a referenced generation or retained obsolete state')
    resume(path)
    for consumer in context['records'].values():
        verify_served(('127.0.0.1', consumer['tls_port']), expected, 15)
    if host.generations.read()['outcome'] != 'active':
        raise RuntimeError('Consumer activation did not recover after the kernel reboot')
    return compose


def verify_recovery(item):
    path = Path(item['context'])
    context, compose = load_fixture(path)
    os.environ.update(SSL_CERT_FILE=context['trust'], SSL_CERT_DIR='/nonexistent-fixture-ca')
    name = context['records']['mail-admin']['container']
    web = compose.docker.inspect(name)
    if web['State']['Running'] or web['HostConfig']['RestartPolicy']['Name'] != 'no':
        raise RuntimeError('Offline dashboard restarted after a kernel reboot')
    web_root = Path(context['web_root'])
    policy = RuntimePolicy(web_root / 'installation.json',
                           web_root / 'keys' / RUNTIME_POLICY_NAME, os.getuid())
    try:
        policy.require_running()
    except ServiceError as error:
        if error.code != 'operator_maintenance':
            raise
    else:
        raise RuntimeError('Interrupted offline recovery lost its durable closed policy')
    resume(path)
    if compose.docker.inspect(name)['State']['Running']:
        raise RuntimeError('Renewal restarted an offline recovery consumer')
    host = fixture_host(context)
    if host.generations.read()['consumers']['mail-admin']['reason'] != 'offline_recovery':
        raise RuntimeError('Renewal replaced persistent recovery intent')
    return compose


def verify(options):
    current = binding(options)
    record = protected_json(options.directory / 'prepared.json')
    require_matching_source(record)
    if any(record.get(key) != value for key, value in current.items()):
        raise RuntimeError('Kernel fixture bindings changed across interruption')
    require_reboot(record, {'instance_id': options.instance, 'boot_id': boot_id()})
    verify_staging(record)
    activation = verify_activation(record['activation']) if record['activation'] else None
    recovery = verify_recovery(record['recovery']) if record['recovery'] else None
    observation = {**record, 'new_boot_id': boot_id(), 'result': 'pass',
                   'completed_at_utc': timestamp(), 'staging_verified': bool(record['staging']),
                   'consumer_activation_verified': activation is not None,
                   'retention_verified': activation is not None,
                   'offline_recovery_stayed_closed': recovery is not None,
                   'controller_memory': controller_memory()}
    atomic_json(options.directory / 'observation.json', observation)
    if activation is not None:
        activation.close()
    if recovery is not None:
        recovery.close()
    print('Selected kernel scenarios verified', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--instance', required=True)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--only', choices=('tls', 'activation', 'recovery'), default='tls',
                        help='Select a bounded scenario; only selected cases can qualify')
    options = parser.parse_args()
    {'prepare': prepare, 'verify': verify}[options.action](options)


if __name__ == '__main__':
    main()
