import copy
import dataclasses
import hashlib
import time
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from helper.protocol import HelperRequest
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.maddy_support import UnprivilegedMaddyFixture
from tests.recovery_case_support import RecoveryCase
from tests.restore_support import deliver
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_crash_support import DeletionEnabledRuntime


class GuardedDeletionTests(RecoveryCase):
    # The dashboard runs as the Maddy user, so the shared Maddy data must belong to that user.
    maddy_class = UnprivilegedMaddyFixture

    def setUp(self):
        super().setUp()
        self.email = self.create('guarded-recovery')
        self.body = b'guarded-deletion-mail-must-survive-rejected-requests'
        deliver(self.fixture, self.email, self.body.decode())
        self.archive = self.verified_archive(self.email, self.body)
        self.helper = self.configure_deletion(self.archive)
        self.paths = self.helper.dependencies.deletion.paths
        self.request = HelperRequest('delete', str(uuid.uuid4()), self.email,
                                     cutoff=self.archive.manifest['cutoff'])
        self.helper.dependencies.deletion.check(self.request, self.helper.journal)

    def assert_rejected_without_mutation(self, request):
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            with self.assertRaises((OSError, ServiceError)):
                self.helper.handle(request)
        reads = {('version',), ('creds', 'list'), ('imap-acct', 'list')}
        mutations = [call for call in change.call_args_list if call.args[0] not in reads]
        self.assertEqual(mutations, [])
        self.assertIsNone(self.helper.journal.lookup(request.request_id))
        self.assert_body(self.email, self.body)

    def test_restored_backup_requires_age_incarnation_and_complete_store_coverage(self):
        baseline = self.archive.manifest
        now = time.time()
        future = datetime.fromtimestamp(now + 300, UTC).isoformat()
        self.assert_expired_snapshot_rejected()
        variants = [dict(baseline, cutoff=future)]
        for key, value in (('generation', 'different-incarnation'), ('credentials', False),
                           ('storage', False), ('messages', False)):
            changed = copy.deepcopy(baseline)
            changed['accounts'][self.email][key] = value
            variants.append(changed)
        for manifest in variants:
            with self.subTest(manifest=manifest['accounts'][self.email], cutoff=manifest['cutoff']):
                atomic_json(self.paths.backup, manifest)
                request = dataclasses.replace(self.request, request_id=str(uuid.uuid4()),
                                               cutoff=manifest['cutoff'])
                self.assert_rejected_without_mutation(request)
        atomic_json(self.paths.backup, baseline)
        self.helper.dependencies.deletion.check(self.request, self.helper.journal)

    def assert_expired_snapshot_rejected(self):
        baseline = self.archive.manifest
        guard = self.helper.dependencies.deletion
        original_clock, dependencies = guard.clock, self.archive.backup.dependencies
        policy = protected_json(self.paths.policy)
        observed = datetime.fromisoformat(baseline['cutoff']).timestamp() + 601
        self.write_policy({**policy, 'valid_until': observed + 60})
        guard.clock = lambda: observed
        self.archive.backup.dependencies = dataclasses.replace(dependencies, clock=guard.clock)
        try:
            self.archive.backup.verify_latest()
            self.assert_rejected_without_mutation(self.request)
        finally:
            guard.clock = original_clock
            self.archive.backup.dependencies = dependencies
            atomic_json(self.paths.backup, baseline)
            self.write_policy(policy)

    def start_dashboard(self):
        web = DeletionEnabledRuntime(self.fixture)
        self.addCleanup(web.close)
        web.start()
        authentication = RuntimeAuthentication(self, web)
        authentication.enroll()
        authentication.login()
        authentication.reauthenticate()
        self.assertEqual(web.api('GET', '/api/accounts')[0], 200)
        return web

    def test_https_delete_outliving_the_deadline_never_repeats_removal_and_stays_unresolved(self):
        web = self.start_dashboard()
        payload = {'request_id': str(uuid.uuid4()), 'confirmation': self.email,
                   'acknowledged': True, 'cutoff': web.authorize(self.email)}
        web.hold_after('creds remove')
        _, headers, outcome = web.api('DELETE', '/api/accounts/' + self.email, payload)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertNotEqual(outcome.get('state'), 'complete')
        self.assertTrue(web.held())
        web.clear_hold()
        self.assertEqual(len(web.calls('creds remove')), 1)
        retried = web.api('DELETE', '/api/accounts/' + self.email, payload)[2]
        self.assertNotEqual(retried.get('state'), 'complete')
        self.assertEqual(len(web.calls('creds remove')), 1)
        self.assertEqual(len(web.calls('imap-acct remove')), 0)
        self.assertNotEqual(web.journal().lookup(payload['request_id'])['state'], 'complete')
        self.assertEqual(self.fixture.maddy.inventories()['credentials'], [])

    def test_unavailable_or_tampered_archive_and_forged_cutoff_cannot_authorize_real_deletion(self):
        baseline = self.archive.manifest
        for key in ('verified', 'available', 'restore_passed'):
            with self.subTest(requirement=key):
                atomic_json(self.paths.backup, {**baseline, key: False})
                self.assert_rejected_without_mutation(self.request)
        atomic_json(self.paths.backup, baseline)
        self.assert_rejected_without_mutation(dataclasses.replace(self.request, cutoff='forged'))
        reference = self.archive.backup.dependencies.transfer.path
        archive = reference / (self.archive.snapshot.name + '.encrypted')
        unavailable = archive.with_suffix('.temporarily-unavailable')
        archive.rename(unavailable)
        try:
            with self.assertRaises(OSError):
                self.archive.backup.verify_latest()
            self.assert_rejected_without_mutation(self.request)
        finally:
            unavailable.rename(archive)
        self.archive.backup.verify_latest()
        original = archive.read_bytes()
        archive.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        try:
            with self.assertRaisesRegex(ServiceError, 'off_host_archive_integrity_failed'):
                self.archive.backup.verify_latest()
            self.assert_rejected_without_mutation(self.request)
        finally:
            archive.write_bytes(original)
        self.archive.backup.verify_latest()
        self.helper.dependencies.deletion.check(self.request, self.helper.journal)

    def write_policy(self, policy):
        atomic_json(self.paths.policy, policy)
        deployment = protected_json(self.paths.deployment)
        deployment['inputs']['protected_policy_sha256'] = hashlib.sha256(
            self.paths.policy.read_bytes()).hexdigest()
        atomic_json(self.paths.deployment, deployment)

    def test_missing_stale_changed_or_protected_recipient_policy_blocks_actual_removal(self):
        baseline = protected_json(self.paths.policy)
        variants = [dict(baseline, protected_recipients=[self.email]),
                    dict(baseline, valid_until=time.time() - 1),
                    dict(baseline, reviewed_files={})]
        for policy in variants:
            with self.subTest(policy=policy):
                self.write_policy(policy)
                self.assert_rejected_without_mutation(self.request)
        self.write_policy(baseline)
        retained = self.paths.policy.with_suffix('.retained')
        self.paths.policy.rename(retained)
        try:
            self.assert_rejected_without_mutation(self.request)
        finally:
            retained.rename(self.paths.policy)
        self.paths.policy.chmod(0o644)
        try:
            self.assert_rejected_without_mutation(self.request)
        finally:
            self.paths.policy.chmod(0o600)
        original = self.fixture.configuration.read_bytes()
        self.fixture.configuration.write_bytes(original + b'\n# routing changed after review\n')
        try:
            self.assert_rejected_without_mutation(self.request)
        finally:
            self.fixture.configuration.write_bytes(original)
        self.helper.dependencies.deletion.check(self.request, self.helper.journal)

    def test_https_delete_updates_metadata_and_old_backup_cannot_cover_recreated_mail(self):
        web = self.start_dashboard()
        payload = {'request_id': str(uuid.uuid4()), 'confirmation': self.email,
                   'acknowledged': True, 'cutoff': web.authorize(self.email)}
        status, _, outcome = web.api('DELETE', '/api/accounts/' + self.email, payload)
        self.assertEqual((status, outcome['state']), (200, 'complete'))
        self.assertEqual(self.fixture.maddy.inventories(), {'credentials': [], 'storage': []})
        with web.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM mailboxes').fetchone()[0], 0)
        recreated = self.create('guarded-recovery')
        self.assertEqual(recreated, self.email)
        old_generation = self.archive.manifest['accounts'][self.email]['generation']
        self.assertNotEqual(self.helper.journal.incarnation(self.email)['generation'],
                            old_generation)
        deliver(self.fixture, self.email, self.body.decode())
        request = dataclasses.replace(self.request, request_id=str(uuid.uuid4()))
        self.assert_rejected_without_mutation(request)
