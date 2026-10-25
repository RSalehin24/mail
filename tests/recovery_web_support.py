"""Trusted local HTTPS and matched-image assertions for full-host recovery fixtures."""

import json
import stat
import subprocess
import time
from pathlib import Path

from tests.fixture_values import REPLACEMENT_PASSWORD
from tests.host_factor_support import totp
from tests.host_https_support import request
from tests.host_tls_support import recovery_context

AUTHENTICATION_CHECK = """import json,sys
from argon2.exceptions import VerifyMismatchError
from backend.services import Services
old=json.load(sys.stdin)
services=Services.operator()
try:
 auth=services.authentication
 with auth.database.transaction() as db:
  row=db.execute('SELECT * FROM admin_auth WHERE id=1').fetchone()
  assert row['initialized']==1 and row['recovery_required']==0
  try: auth.dependencies.hasher.verify(row['password_hash'],old['password'])
  except VerifyMismatchError: pass
  else: raise RuntimeError('Old master password survived recovery')
  assert auth.keys.open(row['totp_secret'],'totp')!=old['seed']
  verifiers={value[0] for value in db.execute('SELECT verifier FROM recovery_codes')}
  assert len(verifiers)==5
  assert all(auth.keys.digest(code,'recovery') not in verifiers for code in old['codes'])
  for table in ('sessions','auth_challenges','otp_codes'):
   assert db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0
 assert services.relay.reveal()=='synthetic-full-host-relay-secret'
 assert not services.relay.otp_ready()
 print(json.dumps({'old_password_rejected':True,'old_totp_replaced':True,
  'all_old_recovery_codes_invalid':True,'restored_authentication_tokens_absent':True,
  'relay_decrypted_with_matching_keys':True,'relay_readiness_invalidated':True}))
finally: services.close()
"""


def verify_closed_authentication(tools, old):
    arguments = tools.authentication_arguments('probe')
    index = arguments.index(tools.deployment['dashboard_image_id'])
    result = subprocess.run(
        ('docker', 'run', '-i') + arguments[1:index + 1] + ('python', '-c', AUTHENTICATION_CHECK),
        input=json.dumps(old), capture_output=True, text=True, check=False, timeout=60,
    )
    if result.returncode:
        raise RuntimeError('Matched-image recovery authentication assertions failed: '
                           + result.stderr[-2000:])
    return json.loads(result.stdout)


class RecoveredHttps:
    def __init__(self, timeout=5, password=REPLACEMENT_PASSWORD):
        self.context = recovery_context()
        self.cookie, self.timeout = '', timeout
        self.password = password

    def api(self, method, path, payload=None):
        return request((self.context, self.timeout), (method, path, payload), self.cookie)

    def ready(self):
        started = time.monotonic()
        deadline = started + 15
        while time.monotonic() < deadline:
            try:
                if self.api('GET', '/api/status')[0] == 200:
                    return time.monotonic() - started
            except OSError:
                time.sleep(0.1)
        raise RuntimeError('Restored HTTPS did not become ready within fifteen seconds')

    def challenge(self):
        status, _, result = self.api('POST', '/api/login', {
            'email': 'rsalehin24@gmail.com', 'password': self.password,
        })
        if status != 200 or result.get('email_otp_available') is not False:
            raise RuntimeError('Recovered password challenge or closed email OTP failed')
        return result['challenge']

    def verify(self, values):
        return self.api('POST', '/api/2fa/verify', values)

    def login(self, material):
        challenge = self.challenge()
        status, headers, _ = self.verify({'challenge': challenge, 'factor': 'totp',
                                          'code': totp(material['seed'], time.time() + 30)})
        if status != 200:
            raise RuntimeError('Recovered password and replacement factor did not authenticate')
        header = headers.get('set-cookie', headers.get('Set-Cookie', ''))
        if any(value not in header for value in ('Secure', 'HttpOnly', 'SameSite=strict')):
            raise RuntimeError('Recovered HTTPS cookie lost its security attributes')
        self.cookie = header.split(';', 1)[0]
        return self.cookie

    def old_authentication_rejected(self, old):
        self.cookie = old['cookie']
        if self.api('GET', '/api/accounts')[0] != 401:
            raise RuntimeError('An old source cookie authenticated after recovery')
        self.cookie = ''
        status, _, _ = self.api('POST', '/api/login', {
            'email': 'rsalehin24@gmail.com', 'password': old['password'],
        })
        if status != 401:
            raise RuntimeError('The previous master password authenticated after recovery')
        challenge = self.challenge()
        for factor, code in (('recovery', old['codes'][-1]),
                             ('totp', totp(old['seed'], time.time()))):
            status = self.verify({'challenge': challenge, 'factor': factor, 'code': code})[0]
            if status != 401:
                raise RuntimeError('A previous recovery code or TOTP check expected HTTP 401; '
                                   + 'observed HTTP ' + str(status))
        return {'old_cookie_rejected_over_https': True, 'old_password_rejected_over_https': True,
                'old_recovery_code_rejected_over_https': True, 'old_totp_rejected_over_https': True}


def restored_permissions():
    maddy, dashboard = Path('/opt/maddy/data'), Path('/opt/mail-admin/data')
    require_mode(maddy, (0, 0o700))
    require_mode(dashboard, (10001, 0o750))
    private_data(maddy, {'maddy.conf', 'tls'})
    private_data(dashboard, set())
    for directory in (Path('/etc/mail-admin/secrets'), Path('/opt/maddy/tls/fixture')):
        require_mode(directory, (0, 0o750))
        for path in directory.iterdir():
            require_mode(path, (0, 0o640))
            if path.stat().st_gid != 10001:
                raise RuntimeError('Restored key group differs from the dedicated service group')
    require_mode(Path('/etc/mail-admin/installation.json'), (0, 0o640))
    return {'restored_data_permissions_restrictive': True,
            'matching_keys_and_marker_restricted_to_root_and_service_group': True}


def require_mode(path, expected):
    metadata = path.lstat()
    if path.is_symlink() or (metadata.st_uid, stat.S_IMODE(metadata.st_mode)) != expected:
        raise RuntimeError('Restored private application data has unsafe permissions: ' + str(path))


def private_data(directory, mount_placeholders):
    for path in directory.rglob('*'):
        relative = str(path.relative_to(directory))
        if relative in mount_placeholders:
            if path.is_symlink() or (path.is_file() and path.stat().st_size != 0):
                raise RuntimeError('An excluded mount placeholder contains unexpected data')
            continue
        expected = 0o700 if path.is_dir() else 0o600
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != expected:
            raise RuntimeError('Restored private application data has unsafe permissions: '
                               + str(path))
