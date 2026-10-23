import json
import time
from unittest.mock import patch

from shared.errors import ServiceError
from shared.validation import MAIL_HOST
from tests.alert_support import AlertDeliveryFixture
from tests.silent_peer_support import SilentPeer


class ExternalAlertDeliveryTests(AlertDeliveryFixture):
    def test_real_tls_endpoint_outage_and_recovery_are_delivered_retried_in_order_and_deduplicated(
        self,
    ):
        pair = self.issuer.issue()
        peer = self.peer(pair)
        with self.delivery_scope():
            self.monitor.external()
            self.assert_received('recovered', 2)
            peer.close()
            self.smtp.reject_auth = True
            with self.assertRaisesRegex(ServiceError, 'notifications_pending'):
                self.monitor.external()
            failed = json.loads(self.alerts.path.read_bytes())
            self.assertTrue(all(not value['delivered'] for value in failed.values()))
            self.peer(pair)
            with self.assertRaisesRegex(ServiceError, 'notifications_pending'):
                self.monitor.external()
            self.assertEqual(len(self.smtp.messages), 2)
            self.smtp.reject_auth = False
            self.state.now = max(value['next_attempt'] for value in failed.values())
            self.alerts.flush()
            self.assert_received('unreachable_or_untrusted', 2)
            self.assert_received('recovered', 4)
            self.assert_outage_precedes_recovery()
            self.monitor.external()
            self.assertEqual(len(self.smtp.messages), 6)

    def assert_outage_precedes_recovery(self):
        records = json.loads(self.alerts.path.read_bytes())
        for port in (993, 443):
            messages = [message for message in self.smtp.messages
                        if f'external_tls_{port}'.encode() in message]
            self.assertEqual(len(messages), 3)
            self.assertIn(b'unreachable_or_untrusted', messages[1])
            self.assertIn(b'recovered', messages[2])
            self.assertTrue(records[f'external_tls_{port}']['delivered'])
            self.assertEqual(records[f'external_tls_{port}']['pending'], [])

    def test_actual_expired_and_wrong_hostname_tls_do_not_block_independent_smtp_delivery(self):
        for name, values in (('expired', {'expired': True}),
                             ('wrong-host', {'hostname': 'untrusted.example'})):
            with self.subTest(certificate=name):
                self.peer(self.issuer.issue(name, **values))
                with self.delivery_scope():
                    self.monitor.external()
        self.assert_received('unreachable_or_untrusted', 2)
        self.assertEqual(len(self.smtp.messages), 2)

    def test_silent_host_tls_times_out_and_still_delivers_independent_alerts(self):
        silent = SilentPeer('tcp')
        self.addCleanup(silent.close)
        pair = self.issuer.issue()
        peer = self.peer(pair)
        with self.delivery_scope():
            self.monitor.external()
            self.assert_received('recovered', 2)
            peer.close()
            closed = self.target_address

            def outage(endpoint, *arguments, **keywords):
                if endpoint == (MAIL_HOST, 993):
                    return self.connect(silent.address, *arguments, **keywords)
                if endpoint == (MAIL_HOST, 443):
                    return self.connect(closed, *arguments, **keywords)
                return self.route(endpoint, *arguments, **keywords)

            started = time.monotonic()
            with patch('socket.create_connection', side_effect=outage):
                self.monitor.external()
            self.assertTrue(silent.received.is_set())
            self.assertLess(time.monotonic() - started, 7)
            self.assert_received('unreachable_or_untrusted', 2)
            self.peer(pair)
            self.monitor.external()
            self.monitor.external()
            self.assert_received('recovered', 4)
            self.assertEqual(len(self.smtp.messages), 6)
