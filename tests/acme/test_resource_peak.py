"""Measure real HTTPS, root helper, Maddy CLI and an offline utility together."""

import json
import os
import uuid
from pathlib import Path

from tests.resource_support import container_memory, wait_for_probe
from tests.runtime.test_mail_flow import MailFlowFixture
from tools.resources import MADDY_MEMORY_MIB, WEB_MEMORY_MIB, fixture_budget
from tools.run_tests import controller_memory, source_digest

PROBE = '''import signal,sys
from pathlib import Path
from backend.operator import main
sys.argv=['backend.operator','probe','--uid','10001','--gid','10001']
main()
Path('/tmp/resource-probe').write_bytes(b'x'*(2*1024*1024))
print('resource-probe-ready',flush=True)
signal.pause()
'''


class ResourcePeakTests(MailFlowFixture):
    def transient_probe(self):
        arguments = self.web.probe_arguments()
        image = arguments.index(os.environ['MAIL_ADMIN_RUNTIME_REFERENCE'])
        name = 'mail-admin-resource-probe-' + str(uuid.uuid4())
        command = ('create', '--name', name, '--label', 'mail-admin.fixture=true')
        command += arguments[2:image + 1] + ('python', '-c', PROBE)
        identifier = self.web.command(command).stdout.strip()
        self.addCleanup(self.web.command, ('rm', '--force', identifier))
        self.web.command(('start', identifier))
        wait_for_probe(self.web, identifier)
        return identifier

    def test_combined_peak_includes_tmpfs_helper_cli_and_transient_work_within_budget(self):
        transient = self.transient_probe()
        self.web.command(('exec', self.web.identifier, 'python', '-c',
                          "from pathlib import Path; "
                          "Path('/tmp/resource-web').write_bytes(b'x'*(2*1024*1024))"))
        self.maddy.command(('exec', self.maddy.identifier, '/bin/sh', '-c',
                            'dd if=/dev/zero of=/run/maddy/resource-fixture bs=1024 count=256'))
        self.mutation('/api/accounts/create', {
            'request_id': str(uuid.uuid4()), 'email': 'resource-fixture@rsalehin24.me',
            'password': ' measured real helper and CLI work ', 'display_name': '', 'notes': '',
        })
        observed = {
            'controller_including_helper': controller_memory(),
            'web': container_memory(self.web, self.web.identifier),
            'maddy_including_cli': container_memory(self.maddy, self.maddy.identifier),
            'transient_utility': container_memory(self.web, transient),
        }
        self.assertEqual([observed[name]['limit_bytes'] for name in (
            'web', 'maddy_including_cli', 'transient_utility',
        )], [WEB_MEMORY_MIB * 1024**2, MADDY_MEMORY_MIB * 1024**2, WEB_MEMORY_MIB * 1024**2])
        self.assertGreaterEqual(observed['web']['tmpfs_charged_bytes'], 2 * 1024**2)
        self.assertGreaterEqual(observed['transient_utility']['tmpfs_charged_bytes'], 2 * 1024**2)
        self.assertGreaterEqual(observed['maddy_including_cli']['tmpfs_charged_bytes'], 256 * 1024)
        self.assertTrue(all(record.get('oom_kill', 0) == 0 for record in observed.values()))
        upper_bound = sum(record['peak_bytes'] for record in observed.values())
        budget = fixture_budget('qualification')
        self.assertLess(upper_bound, (budget.controller_mib + budget.companions_mib) * 1024**2)
        record = {'observations': observed, 'sum_of_component_peaks_bytes': upper_bound,
                  'host_reserve_bytes': budget.reserve_mib * 1024**2,
                  'recorded_fixture_budget_bytes': budget.minimum_available_bytes,
                  'source_sha256': source_digest(),
                  'scope': 'Conservative simultaneous upper bound; tmpfs is included in cgroups'}
        path = Path('build') / ('combined-resource-observation-' + str(uuid.uuid4()) + '.json')
        path.write_text(json.dumps(record, indent=2) + '\n')
