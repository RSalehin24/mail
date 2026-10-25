"""Run existing Chrome against the actual HTTPS image; frozen evidence requires exact bindings."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.browser_inputs import runner_inputs  # noqa: E402
from tools.qualification_bundle import QualificationBundle  # noqa: E402
from tools.resources import require_fixture_disk  # noqa: E402
from tools.run_linux import docker  # noqa: E402
from tools.run_remote import image_observation, image_snapshot, verify_images  # noqa: E402
from tools.run_tests import source_digest, timestamp  # noqa: E402


def controller_arguments(images, directory, volume):
    point = json.loads(docker(('volume', 'inspect', volume)))[0]['Mountpoint']
    return (
        'create', '--name', 'mail-admin-browser-controller-' + str(uuid.uuid4()),
        '--label', 'mail-admin.fixture=true', '--platform', 'linux/amd64',
        '--memory', '192m', '--memory-swap', '192m', '--cpus', '0.5', '--pids-limit', '128',
        '--network', 'host', '--mount', 'type=volume,src=' + volume + ',dst=/fixtures',
        '--mount', 'type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock',
        '--mount', 'type=bind,src=' + str(ROOT) + ',dst=/app,readonly',
        '--mount', 'type=bind,src=' + str(ROOT / 'build') + ',dst=/app/build',
        '-e', 'MAIL_ADMIN_FIXTURE_ROOT=/fixtures', '-e', 'TMPDIR=/fixtures',
        '-e', 'MAIL_ADMIN_FIXTURE_DAEMON_ROOT=' + point,
        '-e', 'MAIL_ADMIN_RUNTIME_REFERENCE=' + images['mail-admin:candidate'],
        '-e', 'MAIL_ADMIN_RUNTIME_IMAGE=' + images['mail-admin:candidate'],
        images['mail-admin:qualification'], 'python', 'tools/https_browser_fixture.py',
        '/app/build/' + str(directory.relative_to(ROOT / 'build')),
    )


def wait_material(directory, controller):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        state = json.loads(docker(('inspect', controller)))[0]['State']
        if not state['Running']:
            raise RuntimeError('The actual HTTPS browser fixture could not start')
        path = directory / 'material.json'
        if path.is_file():
            return json.loads(path.read_bytes())
        time.sleep(0.1)
    raise RuntimeError('The browser fixture did not publish its bounded readiness observation')


def run_browser(directory, runner):
    with (directory / 'browser-driver.log').open('x') as writer:
        result = subprocess.run(  # noqa: S603, S607 -- existing local Node and reviewed script.
            (runner['node']['path'], str(ROOT / 'tools/check_https_browser.mjs'), str(directory)),
            env={**os.environ, 'MAIL_ADMIN_CHROME': runner['chrome']['path']},
            stdout=writer, stderr=subprocess.STDOUT, check=False, timeout=300,
        )
    return result.returncode


def execute(evidence):
    require_fixture_disk(ROOT)
    images, source = image_snapshot(), source_digest()
    runner = runner_inputs()
    bundle = QualificationBundle(evidence) if evidence is not None else None
    if bundle is not None:
        bundle.verify_source()
        verify_images(images)
    elif (image_observation(images['mail-admin:candidate'])['runtime_sha256']
          != image_observation(images['mail-admin:qualification'])['runtime_sha256']):
        raise ValueError('Development runtime and controller application files differ')
    parent = evidence.resolve().parent if evidence is not None else ROOT / 'build'
    directory = parent / ('https-browser-' + str(uuid.uuid4()))
    directory.mkdir(mode=0o700)
    volume = 'mail-admin-browser-' + str(uuid.uuid4())
    docker(('volume', 'create', '--label', 'mail-admin.fixture=true', volume))
    controller = docker(controller_arguments(images, directory, volume))
    record = {'scope': 'frozen-https-browser' if bundle else 'development-https-browser',
              'source_sha256': source, 'images': images, 'started_at_utc': timestamp(),
              'browser_system': platform.system(), 'browser_machine': platform.machine(),
              'controller_id': controller, 'result': 'fail',
              'runner_inputs': runner,
              'identity': bundle.identity if bundle else None}
    try:
        docker(('start', controller))
        material = wait_material(directory, controller)
        code = run_browser(directory, runner)
        (directory / 'stop').touch(mode=0o600)
        exit_code = docker(('wait', controller))
        observed = json.loads((directory / 'fixture-observation.json').read_bytes())
        browser = json.loads((directory / 'node-observation.json').read_bytes())
        if (code or exit_code != '0' or browser['result'] != 'pass'
                or not observed['secret_logs_absent'] or source != source_digest()
                or observed['source_sha256'] != source or material['source_sha256'] != source):
            raise ValueError('The actual HTTPS browser assertions did not complete successfully')
        state = json.loads(docker(('inspect', controller)))[0]['State']
        if state['OOMKilled']:
            raise ValueError('A killed browser controller cannot qualify assertions')
        if bundle:
            bundle.verify_source()
            bundle.verify_artifacts()
        if image_snapshot() != images:
            raise ValueError('Frozen image references changed during browser execution')
        if runner_inputs() != runner:
            raise ValueError('Browser runner changed during the frozen execution')
        record.update(browser=browser, fixture=observed, result='pass')
    finally:
        record['completed_at_utc'] = timestamp()
        with (directory / 'observation.json').open('x') as writer:
            json.dump(record, writer, indent=2)
        (directory / 'stop').touch(mode=0o600, exist_ok=True)
        if json.loads(docker(('inspect', controller)))[0]['State']['Running']:
            docker(('stop', '--time', '45', controller))
        docker(('rm', controller))
        docker(('volume', 'rm', volume))
        print(json.dumps({'result': record['result'], 'directory': str(directory)}), flush=True)
    if bundle:
        digest = hashlib.sha256((directory / 'observation.json').read_bytes()).hexdigest()
        reference = {'report': str((directory / 'observation.json').relative_to(parent)),
                     'sha256': digest}
        with (parent / 'browser-observation-reference.json').open('x') as writer:
            json.dump(reference, writer)
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    directory = execute(parser.parse_args().evidence)
    print(directory / 'observation.json')


if __name__ == '__main__':
    main()
