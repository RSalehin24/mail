"""Root-only synchronization of native Cloudflare forwarding and local IMAP delivery."""

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from helper.forwarding import ForwardingControl, destination
from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock
from shared.errors import ServiceError
from shared.files import atomic_json, protected_bytes, protected_json
from shared.validation import address

CONFIGURATION = Path('/etc/mail-admin/cloudflare-forwarding.json')
OBSERVATION = 'cloudflare-forwarding.json'
MANAGED = 'Mail Admin local delivery: '
PRESERVED = {'mail@rsalehin24.me', 'website@rsalehin24.me'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, response: Any, code: int, message: str,
                         headers: Any, url: str) -> None:
        raise ServiceError('cloudflare_redirect_rejected', 503)


class Cloudflare:
    def __init__(self, account: str, zone: str, token: str) -> None:
        if not all(re.fullmatch('[a-f0-9]{32}', item) for item in (account, zone)):
            raise ServiceError('invalid_cloudflare_account', 503)
        self.account, self.zone, self.token = account, zone, token

    def request(self, method: str, path: str, body: Any = None,
                content_type: str = 'application/json') -> Any:
        if method not in {'GET', 'POST', 'PUT'} or not path.startswith(
            (f'/accounts/{self.account}/', f'/zones/{self.zone}/')
        ) or '..' in path or '://' in path:
            raise ServiceError('invalid_cloudflare_operation', 503)
        payload = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request('https://api.cloudflare.com/client/v4' + path,
            data=payload, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': content_type})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
                raw = response.read(2 * 1024**2 + 1)
            if len(raw) > 2 * 1024**2:
                raise ServiceError('cloudflare_response_limit', 503)
            result = json.loads(raw)
        except urllib.error.HTTPError as error:
            raise ServiceError('cloudflare_credentials_rejected' if error.code in (401, 403)
                               else 'cloudflare_request_failed', 503) from None
        except (OSError, ValueError) as error:
            raise ServiceError('cloudflare_unavailable', 503) from error
        if not isinstance(result, dict) or result.get('success') is not True:
            raise ServiceError('cloudflare_request_failed', 503)
        return result

    def listing(self, path: str) -> list[dict[str, Any]]:
        rows = []
        for page in range(1, 11):
            data = self.request('GET', path + f'?per_page=100&page={page}')
            values = data.get('result')
            if not isinstance(values, list):
                raise ServiceError('cloudflare_response_invalid', 503)
            rows.extend(values)
            if page >= data.get('result_info', {}).get('total_pages', 1):
                return rows
        raise ServiceError('cloudflare_resource_limit', 503)

    def publish(self, name: str, script: str) -> None:
        if not re.fullmatch('[a-z0-9-]{1,63}', name):
            raise ServiceError('invalid_worker_name', 503)
        boundary = 'mailadmin-' + uuid.uuid4().hex
        metadata = json.dumps({'main_module': 'worker.js', 'compatibility_date': '2026-10-01'})
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="metadata"\r\n'
                f'Content-Type: application/json\r\n\r\n{metadata}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="worker.js"; '
                f'filename="worker.js"\r\nContent-Type: application/javascript+module\r\n\r\n'
                f'{script}\r\n--{boundary}--\r\n').encode()
        self.request('PUT', f'/accounts/{self.account}/workers/scripts/{name}', body,
                     'multipart/form-data; boundary=' + boundary)


def worker_script(routes: dict[str, dict[str, str | None]]) -> str:
    return 'const routes = ' + json.dumps(routes, sort_keys=True) + ''';
export default {
  async email(message) {
    const recipient = message.to.toLowerCase();
    if (!Object.hasOwn(routes, recipient)) {
      message.setReject("Mailbox is not configured");
      return;
    }
    const route = routes[recipient];
    // Use native forwarding. Never rebuild the original MIME message.
    const deliveries = [message.forward(route.local)];
    if (route.destination) deliveries.push(message.forward(route.destination));
    await Promise.all(deliveries);
  }
};
'''


def validate_configuration(values: dict[str, Any]) -> None:
    if values.get('domain') != 'rsalehin24.me' or values.get('delivery_domain') != (
        'delivery.rsalehin24.me') or values.get('worker') != 'mail-admin-incoming':
        raise ServiceError('invalid_forwarding_configuration', 503)
    for field in ('account_id', 'zone_id'):
        if not isinstance(values.get(field), str) or not re.fullmatch(
            '[a-f0-9]{32}', values[field]):
            raise ServiceError('invalid_cloudflare_account', 503)
    if type(values.get('activation_verified')) is not bool:
        raise ServiceError('forwarding_activation_verification_required', 503)
    if type(values.get('local_delivery_ready')) is not bool:
        raise ServiceError('local_delivery_verification_required', 503)
    if values.get('token_reference') != '/etc/mail-admin/secrets/cloudflare-forwarding.token':
        raise ServiceError('invalid_forwarding_secret_reference', 503)


class CloudflareSync:
    def __init__(self, control: ForwardingControl, api: Cloudflare,
                 configuration: dict[str, Any]) -> None:
        validate_configuration(configuration)
        self.control, self.api, self.configuration = control, api, configuration
        self.observation = control.root / OBSERVATION

    def run(self, mailboxes: list[str]) -> dict[str, Any]:
        desired = self.control.listing()
        revision = desired['revision']
        emails = sorted({address(value) for value in mailboxes})
        if len(emails) > 256:
            raise ServiceError('forwarding_mailbox_limit', 503)
        addresses = self.api.listing(f'/accounts/{self.api.account}/email/routing/addresses')
        verified = {row['email'] for row in addresses if row.get('verified')}
        known = {row['email'] for row in addresses}
        if self.configuration['local_delivery_ready']:
            for email in emails:
                alias = email.split('@', 1)[0] + '@' + self.configuration['delivery_domain']
                if alias not in known:
                    self.api.request('POST',
                                     f'/accounts/{self.api.account}/email/routing/addresses',
                                     {'email': alias})
                    known.add(alias)
        # Explicit user requests initiate external destination verification once.
        with self.control.connect() as db:
            requests = db.execute("SELECT destination FROM verifications WHERE state='queued'")
            pending = [row[0] for row in requests]
        for recipient in pending:
            if recipient not in known:
                with self.control.connect() as db:
                    db.execute("UPDATE verifications SET state='requesting' WHERE destination=?",
                               (recipient,))
                try:
                    self.api.request('POST',
                                     f'/accounts/{self.api.account}/email/routing/addresses',
                                     {'email': recipient})
                except ServiceError:
                    with self.control.connect() as db:
                        db.execute("UPDATE verifications SET state='uncertain' WHERE destination=?",
                                   (recipient,))
                    raise
                known.add(recipient)
            with self.control.connect() as db:
                db.execute('UPDATE verifications SET state=? WHERE destination=?',
                           ('verified' if recipient in verified else 'sent', recipient))
        rules = {row['email']: row for row in desired['rules']}
        routes = {}
        states = {}
        for email in emails:
            local = email.split('@', 1)[0] + '@' + self.configuration['delivery_domain']
            rule = rules.get(email)
            target = destination(rule['destination']) if rule and rule['enabled'] else None
            ready = local in verified and self.configuration['local_delivery_ready']
            states[email] = {'local_delivery_verified': bool(ready),
                             'destination_verified': bool(target and target in verified)}
            if ready:
                routes[email] = {'local': local,
                                 'destination': target if target in verified else None}
        script = worker_script(routes)
        sha = hashlib.sha256(script.encode()).hexdigest()
        previous = protected_json(self.observation, self.control.root.stat().st_uid) if (
            self.observation.exists()) else {}
        existing = self.api.listing(f'/zones/{self.api.zone}/email/routing/rules')
        replacements = []
        for email in routes:
            matching = [row for row in existing if any(
                match.get('field') == 'to' and match.get('value') == email
                for match in row.get('matchers', []))]
            if len(matching) > 1:
                raise ServiceError('cloudflare_rule_conflict', 503)
            old = matching[0] if matching else None
            if old and not old.get('name', '').startswith(MANAGED):
                if email not in PRESERVED or not self.configuration['activation_verified']:
                    continue
                if old.get('actions') != [{'type': 'forward', 'value': ['rsalehin24@gmail.com']}]:
                    raise ServiceError('cloudflare_existing_route_changed', 503)
            replacements.append((email, old))
        if replacements and previous.get('script_sha256') != sha:
            self.api.publish(self.configuration['worker'], script)
        connected = []
        for email, old in replacements:
            action = [{'type': 'worker', 'value': [self.configuration['worker']]}]
            if old is None or old.get('actions') != action or old.get('enabled') is not True:
                values = {'enabled': True, 'name': MANAGED + email,
                          'actions': action, 'matchers': [
                              {'type': 'literal', 'field': 'to', 'value': email}]}
                path = f'/zones/{self.api.zone}/email/routing/rules'
                if old:
                    identifier = str(old.get('id', old.get('tag', '')))
                    if not re.fullmatch('[A-Za-z0-9_-]{1,128}', identifier):
                        raise ServiceError('invalid_cloudflare_rule_identifier', 503)
                    self.api.request('PUT', path + '/' + identifier, values)
                else:
                    self.api.request('POST', path, values)
            connected.append(email)
        all_ready = set(connected) == set(emails) and all(
            not row['enabled'] or states.get(row['email'], {}).get('destination_verified')
            for row in desired['rules'])
        result = {'state': 'connected' if all_ready else 'verification_pending',
                  'configured': True, 'observed_at': time.time(), 'revision': revision,
                  'script_sha256': sha if replacements else previous.get('script_sha256'),
                  'mailboxes': states, 'connected_mailboxes': connected,
                  'verified_destinations': sorted(verified & {
                      row['destination'] for row in desired['rules']})}
        atomic_json(self.observation, result)
        return result


def main() -> None:
    if os.geteuid() != 0:
        raise ServiceError('root_required', 403)
    from host.context import HostContext

    context = HostContext.load()
    control = ForwardingControl(Path('/var/lib/mail-admin-helper'),
                                Path(context.deployment['maddy_data_directory']))
    provision_lock(control.root / 'forwarding-sync.lock')
    with HostLock(control.root / 'forwarding-sync.lock', LockPolicy(0.1)):
        if not CONFIGURATION.exists():
            atomic_json(control.root / OBSERVATION, {'configured': False, 'state': 'not_connected',
                        'reason': 'cloudflare_credentials_required', 'observed_at': time.time()})
            return
        configuration = protected_json(CONFIGURATION)
        validate_configuration(configuration)
        try:
            token = protected_bytes(Path(configuration['token_reference'])).decode().strip()
            api = Cloudflare(configuration['account_id'], configuration['zone_id'], token)
            with context.coordination.consistent():
                with sqlite3.connect(control.data.joinpath('imapsql.db').as_uri() + '?mode=ro',
                                     uri=True) as db:
                    mailboxes = [row[0] for row in db.execute('SELECT username FROM users')]
                with sqlite3.connect(control.data.joinpath('credentials.db').as_uri() + '?mode=ro',
                                     uri=True) as db:
                    credentials = {row[0] for row in db.execute('SELECT key FROM passwords')}
                mailboxes = [email for email in mailboxes if email in credentials]
            CloudflareSync(control, api, configuration).run(mailboxes)
        except (OSError, ServiceError) as error:
            path = control.root / OBSERVATION
            previous = protected_json(path) if path.exists() else {}
            previous.update(state='sync_failed', observed_at=time.time(),
                            reason=error.code if isinstance(error, ServiceError)
                            else 'cloudflare_unavailable')
            atomic_json(path, previous)
            raise


if __name__ == '__main__':
    main()
