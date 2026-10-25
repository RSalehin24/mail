import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.browser_inputs import executable_identity, runner_inputs


class BrowserInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        chrome = root / 'Contents/MacOS/Chrome'
        framework = (root / 'Contents/Frameworks/Google Chrome Framework.framework/Versions'
                     / 'Current/Google Chrome Framework')
        self.paths = {'node': root / 'node', 'chrome': chrome, 'chrome_framework': framework}
        for role, path in self.paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('approved ' + role).encode())
        self.expected = {role: executable_identity(path) for role, path in self.paths.items()}
        for role in ('node', 'chrome'):
            self.expected[role]['version'] = 'approved ' + role
        inputs = root / 'inputs.json'
        inputs.write_text(json.dumps(self.expected))
        boundary = patch('tools.browser_inputs.INPUTS', inputs)
        boundary.start()
        self.addCleanup(boundary.stop)
        environment = patch.dict(os.environ, {'MAIL_ADMIN_CHROME': str(chrome)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_changed_framework_cannot_execute_or_reuse_a_previous_runner_version(self):
        self.paths['chrome_framework'].write_bytes(b'changed framework after freeze')
        with (patch('tools.browser_inputs.shutil.which', return_value=str(self.paths['node'])),
              patch('tools.browser_inputs.version') as version,
              self.assertRaisesRegex(RuntimeError, 'files differ')):
            runner_inputs()
        version.assert_not_called()

    def test_matching_binary_hashes_and_versions_are_required_together(self):
        with (patch('tools.browser_inputs.shutil.which', return_value=str(self.paths['node'])),
              patch('tools.browser_inputs.version', side_effect=[
                  'approved node', 'approved chrome',
              ])):
            self.assertEqual(runner_inputs(), self.expected)
        with (patch('tools.browser_inputs.shutil.which', return_value=str(self.paths['node'])),
              patch('tools.browser_inputs.version', return_value='foreign version'),
              self.assertRaisesRegex(RuntimeError, 'binaries differ')):
            runner_inputs()
