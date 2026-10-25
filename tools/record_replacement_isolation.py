"""Observe the exact owned source staying unavailable through a different guest's recovery."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.qualification_bundle import QualificationBundle, validate_report_time  # noqa: E402
from tools.run_local_mock import LocalMock  # noqa: E402
from tools.run_tests import source_digest, timestamp  # noqa: E402


def write_observation(path, record):
    with path.open('x') as writer:
        json.dump(record, writer, indent=2)
        writer.write('\n')


def require_capture(record, expected):
    identity, instance = expected
    if (record.get('scope') != 'frozen-disposable-source-capture'
            or record.get('result') != 'pass' or record.get('source_instance_id') != instance
            or record.get('source_sha256') != source_digest()
            or any(record.get(key) != value for key, value in identity.items())):
        raise ValueError('A matching frozen off-server capture must pass before source outage')


def require_unavailable(outage, observations):
    source, target = observations
    if source['State']['Running'] or source['Id'] == target['Id']:
        raise ValueError('The source must be unavailable on a different controller')
    before = outage['source_initial_stopped_state']
    if any(before[key] != source['State'][key] for key in ('StartedAt', 'FinishedAt')):
        raise ValueError('The original source restarted during replacement recovery')
    if not target['State']['Running'] or target['State']['OOMKilled']:
        raise ValueError('The exact replacement controller must be running without OOM')
    source_volumes = {mount.get('Name') for mount in source['Mounts']
                      if mount['Type'] == 'volume'}
    if any(mount.get('Name') in source_volumes for mount in target['Mounts']):
        raise ValueError('The original source disk is attached to the replacement')


def stop_source(options, bundle):
    source = LocalMock(options.source)
    if not source.inspect()['State']['Running']:
        raise ValueError('A source already stopped requires its original outage observation')
    capture = json.loads(options.capture.read_bytes())
    require_capture(capture, (bundle.identity, source.record['id']))
    validate_report_time(bundle.context, capture)
    if options.record.exists():
        raise ValueError('An existing source outage observation cannot be overwritten')
    record = {**bundle.identity, 'scope': 'frozen-disposable-source-outage',
              'source_sha256': source_digest(), 'source_instance_id': source.record['id'],
              'outage_started_at_utc': timestamp()}
    result = subprocess.run(  # noqa: S603 -- exact validated owned mock's SSH proxy only.
        source.ssh_arguments() + ('sudo -n systemctl --no-block poweroff',),
        capture_output=True, text=True, check=False, timeout=60,
    )
    deadline = time.monotonic() + 45
    observed = source.inspect()
    while observed['State']['Running'] and time.monotonic() < deadline:
        time.sleep(0.25)
        observed = source.inspect()
    record.update(source_initial_stopped_state=observed['State'],
                  shutdown_command_exit_code=result.returncode,
                  result='fail' if observed['State']['Running'] else 'pass')
    write_observation(options.record, record)
    if record['result'] != 'pass':
        raise RuntimeError('The source did not stop at the original bounded shutdown deadline')


def observe(options, bundle):
    source, target = LocalMock(options.source), LocalMock(options.target)
    outage = json.loads(options.record.read_bytes())
    if (outage.get('scope') != 'frozen-disposable-source-outage'
            or outage.get('result') != 'pass'
            or outage['source_instance_id'] != source.record['id']
            or outage['source_sha256'] != source_digest()
            or any(outage.get(key) != value for key, value in bundle.identity.items())):
        raise ValueError('A matching observed source outage is required')
    source_observed, target_observed = source.inspect(), target.inspect()
    require_unavailable(outage, (source_observed, target_observed))
    path = '/root/mail-admin-fixture/replacement-' + target.record['id']
    boot = json.loads(target.guest(('sudo', '-n', 'cat', path + '/boot-observation.json')))
    if (boot['target_instance_id'] != target.record['id']
            or boot['source_instance_id'] != source.record['id']
            or boot['scope'] != 'frozen-disposable-replacement-recovery'
            or boot['source_sha256'] != source_digest()
            or any(boot.get(key) != value for key, value in bundle.identity.items())
            or boot['https_authenticated_after_boot'] is not True):
        raise ValueError('The replacement has not completed matching actual boot validation')
    result = {**bundle.identity, 'scope': 'frozen-disposable-source-isolation',
              'source_sha256': source_digest(), 'source_instance_id': source.record['id'],
              'target_instance_id': target.record['id'], 'source_inspection': source_observed,
              'target_inspection': target_observed,
              'source_initial_stopped_state': outage['source_initial_stopped_state'],
              'outage_started_at_utc': outage['outage_started_at_utc'],
              'started_at_utc': outage['outage_started_at_utc'], 'completed_at_utc': timestamp()}
    write_observation(options.output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stop', 'observe'))
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    if options.action == 'stop' and options.capture is None:
        parser.error('stop requires the verified --capture observation')
    if options.action == 'observe' and (options.target is None or options.output is None):
        parser.error('observe requires --target and a new --output observation')
    bundle = QualificationBundle(options.evidence)
    bundle.verify_source()
    {'stop': stop_source, 'observe': observe}[options.action](options, bundle)


if __name__ == '__main__':
    main()
