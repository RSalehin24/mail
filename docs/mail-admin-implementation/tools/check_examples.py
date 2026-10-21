"""Check shell syntax and Compose examples using explicit non-production fixtures."""

import json
import os
import re
import subprocess

from verification_support import PACKAGE_ROOT, require


def run_command(arguments, content, environment):
    return subprocess.run(arguments, input=content, text=True, capture_output=True,
                          env=environment, timeout=30)


def shell_checks():
    count = 0
    for file in PACKAGE_ROOT.rglob('*.md'):
        for language, content in shell_blocks(file):
            result = run_command([language, '-n'], content, dict(os.environ))
            require(result.returncode == 0, 'Shell example syntax failed')
            count += 1
    return count


def shell_blocks(file):
    blocks = re.findall(r'^```([^\n]*)\n(.*?)^```\s*$', file.read_text(), re.M | re.S)
    return [(language, content) for language, content in blocks if language in {'bash', 'sh'}]


def compose_fixtures():
    return {'MAIL_ADMIN_IMAGE': 'mail-admin:documentation-validation',
            'MAIL_ADMIN_UID': '10001', 'MAIL_ADMIN_GID': '10001',
            'MAIL_ADMIN_MEMORY_LIMIT': '256m', 'MAIL_ADMIN_TMPFS_LIMIT': '16m',
            'MAIL_ADMIN_TLS_DIRECTORY': '/opt/maddy/tls/generations/documentation-validation'}


def validate_mounts(service):
    binds = [item for item in service['volumes'] if item['type'] == 'bind']
    require(len(binds) == 5, 'Unexpected Compose bind count')
    require(all(item['bind']['create_host_path'] is False for item in binds), 'Automatic bind creation')
    require(all(item.get('read_only', False) == (item['target'] != '/app/data') for item in binds),
            'Incorrect bind write permissions')
    require(all('docker.sock' not in item['source'] and '/opt/maddy/data' not in item['source']
                for item in binds), 'Forbidden web-container mount')
    temporary = next(item for item in service['volumes'] if item['type'] == 'tmpfs')
    require(int(temporary['tmpfs']['size']) == 16 * 1024 ** 2 and temporary['tmpfs']['mode'] == 0o1777,
            'Unexpected temporary storage budget/mode')


def validate_service(service):
    require(service['platform'] == 'linux/amd64' and service['pull_policy'] == 'never',
            'Unexpected image architecture/pull policy')
    require(service['user'] == '10001:10001' and service['read_only'] is True,
            'Unexpected user/root-filesystem access')
    require(service['cap_drop'] == ['ALL'] and service['security_opt'] == ['no-new-privileges:true'],
            'Missing privilege restrictions')
    require(int(service['mem_limit']) == 256 * 1024 ** 2, 'Missing memory cap')
    require(service['logging']['options'] == {'max-size': '10m', 'max-file': '3'}, 'Missing log bounds')
    require(service['ports'][0]['published'] == '443' and service['ports'][0]['target'] == 8443,
            'Unexpected port mapping')
    validate_mounts(service)


def reject_missing_values(command, content, environment):
    for missing in compose_fixtures():
        incomplete = {key: value for key, value in environment.items() if key != missing}
        result = run_command(command + ['--quiet'], content, incomplete)
        require(result.returncode != 0, 'Compose accepted a missing required value')
    return len(compose_fixtures())


def compose_checks():
    text = (PACKAGE_ROOT / 'design/runtime.md').read_text()
    blocks = re.findall(r'^```yaml\n(.*?)^```\s*$', text, re.M | re.S)
    require(len(blocks) == 1, 'Expected one authoritative Compose example')
    environment = {key: value for key, value in os.environ.items() if not key.startswith('MAIL_ADMIN_')}
    environment.update(compose_fixtures())
    command = ['docker', 'compose', '--env-file', '/dev/null', '-p', 'mail-admin-plan-validation', '-f', '-', 'config']
    result = run_command(command + ['--format', 'json'], blocks[0], environment)
    require(result.returncode == 0, 'Compose example did not render')
    validate_service(json.loads(result.stdout)['services']['mail-admin'])
    return reject_missing_values(command, blocks[0], environment)


def run_example_checks():
    return {'result': 'pass', 'shell_syntax_checks': shell_checks(),
            'compose_missing_variable_rejections': compose_checks(),
            'scope': 'documentation fixtures; no containers started or bind sources provisioned'}
