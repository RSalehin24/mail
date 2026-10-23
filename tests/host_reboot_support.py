"""Durable before/after observations for a real disposable guest kernel reboot."""

import hashlib
import json
import os
import uuid
from pathlib import Path

from shared.files import atomic_json, protected_json
from tests.host_support import GuestHelper
from tests.host_web_support import GuestDashboard
from tools.qualification_bundle import QualificationBundle
from tools.qualification_host import verify_host_installation
from tools.run_tests import timestamp

REBOOT_POINTER = Path('/root/mail-admin-fixture/current-reboot-intent.json')


def credential_digest(host):
    return hashlib.sha256(json.dumps(host.credentials(), sort_keys=True).encode()).hexdigest()


def prepare_reboot(evidence):
    host = GuestHelper.load()
    bundle = QualificationBundle(evidence)
    bundle.verify_source()
    installed = verify_host_installation(bundle, host)
    web = GuestDashboard(host)
    web.ready()
    if web.api(('GET', '/api/settings/relay'))[0] != 200:
        raise RuntimeError('Authenticated dashboard state must work before reboot')
    record = {'instance_id': host.record['instance_id'], 'boot_id': boot_id(),
              'started_at_utc': timestamp(), 'evidence': str(evidence.resolve()),
              'installed': installed, 'outcome': host.outcome(),
              'credential_rows_sha256': credential_digest(host),
              'containers': container_identities(host), **bundle.identity}
    path = REBOOT_POINTER.with_name('reboot-intent-' + str(uuid.uuid4()) + '.json')
    atomic_json(path, record)
    atomic_json(REBOOT_POINTER, {'path': str(path)})
    return record


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def container_identities(host):
    names = ('maddy', host.record['consumer_name'], 'mail-admin')
    records = json.loads(host.command(('docker', 'inspect') + names))
    if any(not record['State']['Running'] for record in records):
        raise RuntimeError('All three disposable consumers must be running')
    return {record['Name'].removeprefix('/'): {'id': record['Id'], 'image': record['Image']}
            for record in records}


def reboot_intent(host):
    path = Path(protected_json(REBOOT_POINTER)['path'])
    if path.parent != REBOOT_POINTER.parent or path.is_symlink():
        raise RuntimeError('Invalid disposable reboot intent path')
    before = protected_json(path)
    current = {'instance_id': host.record['instance_id'], 'boot_id': boot_id()}
    require_reboot(before, current)
    bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_HOST_EVIDENCE']))
    bundle.verify_source()
    if any(before.get(key) != value for key, value in bundle.identity.items()):
        raise RuntimeError('Reboot intent differs from the frozen artifacts')
    from check_release import validate_report_time

    validate_report_time(bundle.context, {'started_at_utc': before['started_at_utc'],
                                         'completed_at_utc': before['started_at_utc']})
    if verify_host_installation(bundle, host) != before['installed']:
        raise RuntimeError('The installed helper changed across reboot')
    return before


def require_reboot(before, current):
    for record in (before, current):
        for field in ('instance_id', 'boot_id'):
            if str(uuid.UUID(record[field])) != record[field]:
                raise RuntimeError('Invalid observed guest identity')
    if before['instance_id'] != current['instance_id'] or before['boot_id'] == current['boot_id']:
        raise RuntimeError('A new kernel boot on the same disposable instance is required')


def service_observation(host, name):
    raw = host.command(('systemctl', 'show', name,
                        '--property=ActiveState,ExecMainStartTimestampMonotonic,'
                        'ExecMainExitTimestampMonotonic'))
    return dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)


def process_start_us(process):
    values = Path('/proc/' + str(process) + '/stat').read_text().split(') ', 1)[1].split()
    return int(values[19]) * 1_000_000 // os.sysconf('SC_CLK_TCK')
