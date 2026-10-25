"""Actual offline recovery and first registration share the same real maintenance locks."""

import signal
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from host.locks import HostLock
from host.recovery import Recovery
from shared.errors import ServiceError
from tests.offline_consumer_support import OfflineFixtureConsumer
from tests.process_fault_support import kill_at_observed_boundary
from tests.runtime_auth_support import RuntimeAuthentication
from tests.standalone_tls_support import StandaloneTls


def interrupt_recovery(channel, operation):
    def interrupted(_name):
        channel.send(('offline_utility', None))
        signal.pause()
        raise RuntimeError('Recovery unexpectedly continued after the selected interruption')

    with patch.object(operation.consumer, 'offline_utility', side_effect=interrupted):
        operation.run()


class MaintenanceCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.scene = StandaloneTls()
        self.addCleanup(self.scene.close)

    def recovery(self):
        self.scene.start()
        web = self.scene.services.web
        web.port = self.scene.services.records['mail-admin']['tls_port']
        web.certificates.trust = self.scene.mail.certificates.trust
        self.authentication = RuntimeAuthentication(self, web)
        self.authentication.enroll()
        self.authentication.login()
        consumer = OfflineFixtureConsumer(self.scene)
        outcome = self.scene.mail.root / 'recovery-outcome.json'
        outcome.with_suffix('.lock').touch(mode=0o600)
        operation = Recovery(self.scene.coordination, (self.scene.generations, consumer, outcome))
        self.addCleanup(self.discard_utility, operation)
        return operation

    def discard_utility(self, operation):
        if operation.outcome.exists():
            operation.consumer.discard_recovery(operation._record()['utility_name'])

    def test_interruption_keeps_actual_web_closed_against_renewal_and_unverified_resume(self):
        operation = self.recovery()
        kill_at_observed_boundary(interrupt_recovery, operation, ('offline_utility', None))
        with self.assertRaisesRegex(ServiceError, 'operator_maintenance'):
            operation.consumer.policy.require_running()
        self.scene.renewal.retry_activation()
        name = self.scene.services.records['mail-admin']['container']
        observed = self.scene.services.compose.docker.inspect(name)
        self.assertFalse(observed['State']['Running'])
        self.assertEqual(observed['HostConfig']['RestartPolicy']['Name'], 'no')
        self.assertEqual(self.scene.generations.read()['consumers']['mail-admin']['reason'],
                         'offline_recovery')
        with self.assertRaisesRegex(ServiceError, 'recovery_completion_unknown'):
            operation.resume(self.scene.renewal.retry_activation)

    def test_real_terminal_recovery_activates_after_both_shared_locks_are_released(self):
        operation = self.recovery()
        web = self.scene.services.web
        old_cookie = web.cookie
        operation.run()
        name = operation._record()['utility_name']
        self.addCleanup(operation.consumer.tools.discard_recovery, name)

        def activate():
            locks = self.scene.coordination.locks
            with HostLock(locks.renewal, locks.policy), HostLock(locks.maddy, locks.policy):
                self.assertFalse(self.scene.services.compose.docker.inspect(
                    self.scene.services.records['mail-admin']['container'],
                )['State']['Running'])
            with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
                operation.run()
            self.scene.renewal.retry_activation()

        operation.resume(activate)
        self.assertEqual(operation._record()['state'], 'resumed')
        web.cookie = old_cookie
        self.assertEqual(web.api('GET', '/api/accounts')[0], 401)
        self.authentication.codes = list(operation.consumer.material.codes)
        self.authentication.login()
        self.assertEqual(web.api('GET', '/api/accounts')[0], 200)

    def test_first_dashboard_registration_cannot_race_real_renewal_activation(self):
        services = self.scene.services
        original = services.start_mail_only()
        desired = self.scene.generations.stage(self.scene.mail.certificates.issue('next'))
        self.scene.generations.publish(desired)
        lock = self.scene.coordination.locks
        with ThreadPoolExecutor(max_workers=1) as workers:
            with HostLock(lock.maddy, lock.policy):
                future = workers.submit(self.scene.renewal.retry_activation)
                services.deploy('mail-admin', desired)
                self.scene.generations.register('mail-admin')
                self.scene.renewal.consumers = services.consumers()
                active = self.scene.generations.read()['consumers']['maddy']['active_generation']
                self.assertEqual(active, original.name)
                if future.done():
                    with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
                        future.result()
            try:
                future.result(timeout=90)
            except ServiceError as error:
                self.assertEqual(error.code, 'maintenance_busy')
                self.scene.renewal.retry_activation()
        expected = self.scene.generations.validator.validate(desired)['served_sha256']
        self.assertEqual(services.fingerprints(), dict.fromkeys(services.records, expected))
        self.assertEqual(self.scene.generations.read()['outcome'], 'active')
