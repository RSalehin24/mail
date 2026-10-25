"""Validate frozen full-replacement observations and recheck the actual recovered services."""

from datetime import datetime

from host.backup import FULL_STATE_SOURCES
from shared.files import protected_json
from tests.host_reboot_support import require_reboot
from tests.kernel_scenario_support import require_matching_source
from tests.recovery_objective_support import qualification_objectives
from tests.recovery_target_support import RecoveryTarget
from tools.qualification_bundle import validate_report_time


class ReplacementEvidence:
    def __init__(self, bundle, context):
        self.bundle = bundle
        self.target = RecoveryTarget(context)
        self.root = self.target.root

    def bound(self, record, scope):
        if record.get('scope') != scope:
            raise RuntimeError('Development or unrelated recovery observations cannot qualify')
        if any(record.get(key) != value for key, value in self.bundle.identity.items()):
            raise RuntimeError('Recovery observation differs from the frozen artifacts')
        require_matching_source(record)
        return record

    def phase(self, name):
        record = self.bound(protected_json(self.root / (name + '-observation.json')),
                            'frozen-disposable-replacement-recovery')
        if record['phase'] != name:
            raise RuntimeError('A different recovery phase cannot fill the requested observation')
        for key in ('source_instance_id', 'target_instance_id'):
            if record[key] != self.target.context[key]:
                raise RuntimeError('Recovery observation belongs to a different disposable guest')
        stamp = record['observed_at_utc']
        validate_report_time(self.bundle.context, {'started_at_utc': stamp,
                                                   'completed_at_utc': stamp})
        return record

    def source(self):
        record = self.bound(protected_json(self.root / 'source-capture.json'),
                            'frozen-disposable-source-capture')
        validate_report_time(self.bundle.context, record)
        if (record['result'] != 'pass' or record['role_count'] != len(FULL_STATE_SOURCES)
                or record['source_instance_id'] != self.target.context['source_instance_id']
                or record['reference'] != self.target.context['reference']):
            raise RuntimeError('The source capture does not match this complete replacement')
        return record

    def verify_transfer(self):
        source, staged = self.source(), self.phase('stage')
        manifest = source['manifest']
        hashes = (source['content_before_live_delivery'], source['content_after_transfer'],
                   manifest['content_sha256'], staged['content_sha256'])
        if len(set(hashes)) != 1 or not staged['full_state_sources_present']:
            raise RuntimeError('Live source, published snapshot and independent download differ')
        if source['manifest_published_at_ns'] < source['verified_at_ns']:
            raise RuntimeError('The source published before actual off-server verification')
        if not source['r2_host'].endswith('.r2.cloudflarestorage.com'):
            raise RuntimeError('Full replacement must use the approved real off-VM R2 repository')
        if source['message_observation'] != self.target.context['message_observation']:
            raise RuntimeError('The replacement has different before/after-cutoff message markers')
        return source

    def verify_isolation(self):
        record = self.bound(protected_json(self.root / 'source-isolation.json'),
                            'frozen-disposable-source-isolation')
        validate_report_time(self.bundle.context, record)
        source, target = record['source_inspection'], record['target_inspection']
        for key in ('source_instance_id', 'target_instance_id'):
            if record[key] != self.target.context[key]:
                raise RuntimeError('Replacement controller observed another guest')
        if source['State']['Running'] or source['Id'] == target['Id']:
            raise RuntimeError('The original source must remain stopped on a different controller')
        before = record['source_initial_stopped_state']
        if any(before[key] != source['State'][key] for key in ('StartedAt', 'FinishedAt')):
            raise RuntimeError('The source restarted during replacement-server recovery')
        source_volume = 'mail-admin-local-mock-' + record['source_instance_id'] + '-disk'
        if any(mount.get('Name') == source_volume for mount in target['Mounts']):
            raise RuntimeError('The original disk was attached to the replacement guest')
        expected = 'mail-admin-local-mock-' + record['target_instance_id'] + '-disk'
        if [(mount['Type'], mount.get('Name'), mount['Destination'])
            for mount in target['Mounts'] if mount['Type'] != 'tmpfs'] != [
            ('volume', expected, '/fixture'),
        ]:
            raise RuntimeError('The replacement must have only its own empty-start disk')
        return record

    def verify_phases(self):
        names = ('admission', 'stage', 'prepare', 'recover', 'install',
                 'activate', 'verify', 'boot')
        phases = [self.phase(name) for name in names]
        stamps = [record['observed_at'] for record in phases]
        if stamps != sorted(stamps):
            raise RuntimeError('Replacement phases did not execute in the required order')
        admission, staged, prepared, recovered, installed, activated, verified, boot = phases
        required = (admission['empty_target'], staged['full_state_sources_present'],
                    prepared['authentication_prepared'], recovered['private_terminal_completed'],
                    installed['bootstrap_source_matches'], activated['application_started'],
                    activated['relay_decrypted_with_matching_keys'],
                    verified['old_password_rejected_over_https'],
                    boot['https_authenticated_after_boot'],
                    boot['completed_request_retry_did_not_mutate_credentials'])
        if not all(value is True for value in required) or installed['roles_installed'] != 18:
            raise RuntimeError('An actual replacement phase is missing or incomplete')
        if (staged['state'] != 'authentication_closed' or staged['services_started']
                or prepared['services_started'] or recovered['services_started']
                or installed['application_started']):
            raise RuntimeError('The replacement opened before private recovery and installation')
        require_reboot({'instance_id': boot['target_instance_id'], 'boot_id': verified['boot_id']},
                       {'instance_id': boot['target_instance_id'], 'boot_id': boot['boot_id']})
        return boot

    def verify_real_acme_state(self):
        restored = self.target.destination
        if not FULL_STATE_SOURCES <= {path.name for path in restored.iterdir()}:
            raise RuntimeError('One or more actual restored state roles are missing')
        live = list((restored / 'acme/live').glob('*/fullchain.pem'))
        accounts = list((restored / 'acme/accounts').rglob('private_key.json'))
        if len(live) != 1 or not accounts or not (live[0].parent / 'privkey.pem').is_file():
            raise RuntimeError('Full replacement requires a real ACME lineage and account state')
        if live[0].read_bytes() != (restored / 'tls/fixture/fullchain.pem').read_bytes():
            raise RuntimeError('The restored served TLS pair differs from its saved ACME lineage')

    def objectives(self):
        source, isolation, boot = self.source(), self.verify_isolation(), self.phase('boot')
        cutoff = datetime.fromisoformat(source['manifest']['cutoff']).timestamp()
        outage = datetime.fromisoformat(isolation['outage_started_at_utc']).timestamp()
        if datetime.fromisoformat(isolation['completed_at_utc']).timestamp() < boot['observed_at']:
            raise RuntimeError('Source availability was not observed through boot validation')
        limits = self.bundle.context['evidence']['inputs']
        result = qualification_objectives(limits, (cutoff, outage, boot['observed_at']))
        if result['result'] != 'pass':
            raise RuntimeError('Recovery age or elapsed outage exceeds the selected objectives')
        return result
