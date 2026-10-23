"""Observe restored services after a real kernel reboot without starting any consumer."""

import json
from pathlib import Path

from helper.journal import Journal
from host.docker import Docker
from shared.files import protected_json
from tests.recovery_activation_support import credential_digest, verify_mail
from tests.recovery_web_support import RecoveredHttps, restored_permissions

CLIENT = """import json,sys
from pathlib import Path
from backend.helper_client import HelperClient
print(json.dumps(HelperClient(Path('/run/mail-admin-helper/control.sock'),30).call(
 json.load(sys.stdin))))
"""


class RestoredBoot:
    def __init__(self, target):
        self.target = target
        self.profile = protected_json(Path('/etc/mail-admin/deployment.json'))
        self.docker = Docker(timeout=30)

    def verify(self):
        prior = protected_json(self.target.root / 'verify-observation.json')
        current = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if current == prior['boot_id']:
            raise RuntimeError('A real different kernel boot is required')
        self.require_original_credentials()
        for name in ('maddy', 'mail-admin'):
            self.consumer(name)
        checks = self.mail_and_helper()
        web = RecoveredHttps(35)
        readiness = web.ready()
        web.login(protected_json(self.target.root / 'replacement-auth.json'))
        if web.api('GET', '/api/accounts')[0] != 200:
            raise RuntimeError('The recovered dashboard/helper did not survive kernel reboot')
        self.target.record('boot', {**checks, **restored_permissions(), **verify_mail(),
            'previous_boot_id': prior['boot_id'], 'boot_id': current,
            'authentication_method': 'fresh_password_and_totp_after_kernel_boot',
            'https_readiness_seconds': readiness,
            'https_authenticated_after_boot': True, 'services_started_by_observer': False})

    def require_original_credentials(self):
        saved = protected_json(self.target.root / 'activate-observation.json')
        if credential_digest() != saved['credential_digest_before']:
            raise RuntimeError('Restored credentials changed between activation and kernel boot')

    def consumer(self, name):
        observed = self.docker.inspect(name)
        expected = (self.profile['dashboard_image_id'] if name == 'mail-admin'
                    else self.profile['inputs']['maddy_image_id'])
        if (observed['Image'] != expected or not observed['State']['Running']
            or observed['State']['OOMKilled']
            or observed['HostConfig']['RestartPolicy']['Name'] != 'unless-stopped'):
            raise RuntimeError('Restored consumer did not automatically resume its exact image')
        if name == 'mail-admin':
            self.web_consumer(observed)

    @staticmethod
    def web_consumer(observed):
        if (observed['Config']['User'] != '10001:10001'
            or not observed['HostConfig']['ReadonlyRootfs']
            or observed['HostConfig']['CapDrop'] != ['ALL']
            or observed['HostConfig']['Privileged']):
            raise RuntimeError('Restored dashboard privileges changed at boot')
        binding = next(m for m in observed['Mounts']
                       if m['Destination'] == '/run/mail-admin-helper')
        if binding['Source'] != '/run/mail-admin-helper' or binding['RW']:
            raise RuntimeError('Restored helper socket bind changed at boot')

    def client(self, request):
        import subprocess

        result = subprocess.run(('docker', 'exec', '-i', 'mail-admin', 'python', '-c', CLIENT),
                                input=json.dumps(request), capture_output=True, text=True,
                                check=True, timeout=40)
        return json.loads(result.stdout)

    def mail_and_helper(self):
        journal = Journal(Path('/var/lib/mail-admin-helper/journal.db'))
        if journal.unresolved():
            raise RuntimeError('Unresolved restored requests block boot qualification')
        with journal.connection() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM operations ORDER BY id')]
        if len(rows) != 1 or rows[0]['state'] != 'complete':
            raise RuntimeError('Expected the single actual completed synthetic create request')
        before = credential_digest()
        outcome = self.client({'operation': 'outcome', 'request_id': rows[0]['id']})['outcome']
        if outcome != journal.lookup(rows[0]['id']):
            raise RuntimeError('The booted non-root client lost its actual journal outcome')
        self.retry_completed(rows[0])
        if credential_digest() != before:
            raise RuntimeError('An old completed request repeated a credential mutation')
        return {'completed_request_retry_did_not_mutate_credentials': True,
                'completed_request_id': rows[0]['id'], 'journal_unresolved': False,
                'credential_sha256': before}

    def retry_completed(self, row):
        if row['email'] != 'helper-lifecycle@rsalehin24.me' or row['operation'] != 'create':
            raise RuntimeError('Only the recorded synthetic completed create may be retried')
        response = self.client({'operation': 'create', 'request_id': row['id'],
                                'email': row['email'],
                                'password': ' retry must preserve the original mailbox password '})
        if response['state'] != 'complete':
            raise RuntimeError('A completed restored request lost idempotent outcome lookup')
