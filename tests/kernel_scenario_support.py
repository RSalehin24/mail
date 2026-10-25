"""Bind interrupted fixture operations to the same source, guest and a new kernel boot."""

import multiprocessing
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

from helper.journal import Journal
from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import LockPolicy
from shared.files import atomic_json
from tests.tls_crash_support import GenerationObserver, _generation_child
from tools.run_tests import source_digest

STAGING_BOUNDARIES = (('staged', 'fullchain.pem'), ('staged', 'privkey.pem'),
                      ('before_rename', None), ('after_rename', None),
                      ('before_publication', None), ('after_publication', None))


def generation_host(root, trust):
    generations = Generations(GenerationPaths(root / 'generations', root / 'state.json'),
                              (CertificatePolicy(10001, os.getuid()), PairValidator(trust),
                               time.time))
    coordination = Coordination(MaintenanceLocks(
        root / 'renewal.lock', root / 'maddy.lock', LockPolicy(1, os.getuid())),
        (Journal(root / 'journal.db'), generations))
    return SimpleNamespace(generations=generations, coordination=coordination,
                           renewal=SimpleNamespace(consumers={}))


def provision_generations(root, trust):
    root.mkdir(mode=0o700)
    (root / 'generations').mkdir(mode=0o750)
    for name in ('renewal.lock', 'maddy.lock'):
        (root / name).touch(mode=0o600)
    atomic_json(root / 'state.json', {'coordination_migrated': True, 'consumers': {
        name: {'intent': 'never_deployed', 'state': 'absent'}
        for name in ('maddy', 'mail-admin')
    }})
    Journal.provision(root / 'journal.db')
    return generation_host(root, trust)


def pause_at_boundary(action, operation, boundary):
    context = multiprocessing.get_context('fork')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=action, args=(child, operation))
    process.daemon = True
    process.start()
    child.close()
    try:
        if not parent.poll(35) or parent.recv() != boundary:
            raise RuntimeError('Kernel fixture did not reach its exact interruption boundary')
    except BaseException:
        process.kill()
        process.join(5)
        raise
    finally:
        parent.close()
    if not process.is_alive():
        raise RuntimeError('Kernel fixture exited before the power cut')
    return process


def pause_generation(host, source, boundary):
    return pause_at_boundary(_generation_child, (host, source, boundary), boundary)


def activation_child(channel, operation):
    host, boundary = operation
    observer = GenerationObserver(channel, (host, boundary))
    with observer.scope(), host.coordination.consistent():
        host.generations.activate(host.renewal.consumers)
    channel.send(('unexpected_completion', None))


def assert_complete_generations(host):
    complete = []
    for path in host.generations.paths.directory.iterdir():
        if re.fullmatch(r'[0-9a-f]{64}', path.name):
            host.generations.validator.validate(path)
            complete.append(path.name)
    desired = host.generations.read()['desired_generation']
    if desired not in complete:
        raise RuntimeError('Published generation is missing or incomplete after kernel reboot')
    return complete


def require_matching_source(record):
    if record.get('source_sha256') != source_digest():
        raise RuntimeError('Kernel fixture source changed across the interruption')


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
