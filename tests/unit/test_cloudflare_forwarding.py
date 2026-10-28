"""Native forwarding policy, verified destinations and preservation of existing routes."""

import json
import unittest
import uuid

from host.cloudflare_forwarding import CloudflareSync
from shared.errors import ServiceError
from tests.unit import test_forwarding


class FakeCloudflare:
    account, zone = 'a' * 32, 'b' * 32

    def __init__(self):
        self.addresses = [
            {'email': 'mail@delivery.rsalehin24.me', 'verified': '2026-10-28'},
            {'email': 'rsalehin24@gmail.com', 'verified': '2026-10-28'},
        ]
        self.rules = [{'id': 'original', 'name': 'Existing Gmail route', 'enabled': True,
                       'matchers': [{'field': 'to', 'type': 'literal',
                                     'value': 'mail@rsalehin24.me'}],
                       'actions': [{'type': 'forward', 'value': ['rsalehin24@gmail.com']}]}]
        self.published, self.mutations = [], []

    def listing(self, path):
        return self.addresses if path.endswith('addresses') else self.rules

    def request(self, method, path, body=None):
        self.mutations.append((method, path, body))
        return {'success': True, 'result': body}

    def publish(self, name, script):
        self.published.append((name, script))


class CloudflareForwardingTests(unittest.TestCase):
    def setUp(self):
        self.base = test_forwarding.ForwardingTests()
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.control = self.base.control
        self.api = FakeCloudflare()
        self.configuration = {
            'account_id': self.api.account, 'zone_id': self.api.zone,
            'domain': 'rsalehin24.me', 'delivery_domain': 'delivery.rsalehin24.me',
            'worker': 'mail-admin-incoming', 'activation_verified': False,
            'local_delivery_ready': True,
            'token_reference': '/etc/mail-admin/secrets/cloudflare-forwarding.token',
        }
        self.save('rsalehin24@gmail.com')

    def save(self, target, enabled=True):
        return self.control.handle('forwarding_save', str(uuid.uuid4()), {
            'email': 'mail@rsalehin24.me', 'destination': target, 'enabled': enabled,
            'revision': self.control.listing()['revision']}, self.base.fixture.now)

    def sync(self):
        return CloudflareSync(self.control, self.api, self.configuration).run(
            ['mail@rsalehin24.me'])

    def test_existing_gmail_route_is_untouched_until_activation_was_verified(self):
        result = self.sync()
        self.assertEqual(result['state'], 'verification_pending')
        self.assertEqual(self.api.mutations, [])
        self.assertEqual(self.api.published, [])

    def test_connected_rule_preserves_original_message_and_saves_locally_first(self):
        self.configuration['activation_verified'] = True
        result = self.sync()
        self.assertEqual(result['state'], 'connected')
        self.assertEqual(self.api.mutations[0][0], 'PUT')
        script = self.api.published[0][1]
        self.assertIn('message.forward(route.local)', script)
        self.assertIn('message.forward(route.destination)', script)
        self.assertIn('await Promise.all(deliveries)', script)
        self.assertNotIn('message.raw', script)
        self.assertNotIn('EmailMessage', script)
        self.assertTrue(self.control.listing()['rules'][0]['destination_verified'])
        self.assertTrue(self.control.listing()['rules'][0]['synchronized'])

    def test_disable_remove_and_unverified_destinations_keep_local_delivery_only(self):
        self.configuration['activation_verified'] = True
        for action in ('disable', 'remove', 'unverified'):
            with self.subTest(action=action):
                self.save('rsalehin24@gmail.com')
                self.sync()
                if action == 'remove':
                    self.control.handle('forwarding_remove', str(uuid.uuid4()), {
                        'email': 'mail@rsalehin24.me',
                        'revision': self.control.listing()['revision']}, self.base.fixture.now)
                else:
                    self.save('unverified@example.com' if action == 'unverified'
                              else 'rsalehin24@gmail.com', enabled=action != 'disable')
                self.sync()
                source = self.api.published[-1][1]
                values = json.loads(source.split(' = ', 1)[1].split(';', 1)[0])
                self.assertEqual(values['mail@rsalehin24.me'], {
                    'local': 'mail@delivery.rsalehin24.me', 'destination': None})

    def test_unverified_local_delivery_never_replaces_the_existing_route(self):
        self.configuration['activation_verified'] = True
        self.configuration['local_delivery_ready'] = False
        result = self.sync()
        self.assertEqual(result['state'], 'verification_pending')
        self.assertFalse(self.api.mutations)
        self.assertFalse(self.api.published)

    def test_conflicting_existing_route_is_never_overwritten(self):
        self.configuration['activation_verified'] = True
        self.api.rules[0]['actions'][0]['value'] = ['different@example.com']
        with self.assertRaisesRegex(ServiceError, 'cloudflare_existing_route_changed'):
            self.sync()
        self.assertFalse(self.api.mutations)

    def test_verification_is_explicit_and_does_not_resend_existing_requests(self):
        self.save('new@example.com')
        self.sync()
        self.assertFalse(self.api.mutations)
        self.control.handle('forwarding_verify', str(uuid.uuid4()),
                            {'email': 'mail@rsalehin24.me'}, self.base.fixture.now)
        self.sync()
        self.assertEqual(len(self.api.mutations), 1)
        self.assertEqual(self.api.mutations[0][2], {'email': 'new@example.com'})
        self.sync()
        self.assertEqual(len(self.api.mutations), 1)
        self.assertEqual(self.control.listing()['rules'][0]['verification_state'], 'sent')

    def test_new_settings_cannot_claim_the_old_observation_is_synchronized(self):
        self.configuration['activation_verified'] = True
        self.sync()
        self.save('new@example.com')
        value = self.control.listing()
        self.assertEqual(value['connection']['state'], 'pending')
        self.assertFalse(value['rules'][0]['synchronized'])

    def test_new_mailbox_registers_its_delivery_address_and_waits_for_confirmation(self):
        result = CloudflareSync(self.control, self.api, self.configuration).run(
            ['mail@rsalehin24.me', 'new-mailbox@rsalehin24.me'])
        self.assertIn(('POST', '/accounts/' + self.api.account + '/email/routing/addresses',
                       {'email': 'new-mailbox@delivery.rsalehin24.me'}), self.api.mutations)
        self.assertFalse(result['mailboxes']['new-mailbox@rsalehin24.me'][
            'local_delivery_verified'])
        self.assertNotIn('new-mailbox@rsalehin24.me', result['connected_mailboxes'])
        self.assertFalse(any(path.endswith('/email/routing/rules')
                             for _, path, _ in self.api.mutations))
