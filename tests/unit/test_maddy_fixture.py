import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools.maddy_fixture import (
    MADDY_FIXTURE_CONFIGURATION,
    MADDY_FIXTURE_IMAGE,
    fixture_reference,
)


def observed(identity, architecture='amd64'):
    return SimpleNamespace(returncode=0, stdout=json.dumps([
        {'Id': identity, 'Os': 'linux', 'Architecture': architecture},
    ]))


class MaddyFixtureIdentityTests(unittest.TestCase):
    def test_classic_store_selects_only_the_configuration_from_the_approved_oci_archive(self):
        missing = SimpleNamespace(returncode=1, stdout='')
        with patch('tools.maddy_fixture.subprocess.run', side_effect=[
            missing, observed(MADDY_FIXTURE_CONFIGURATION),
        ]) as run:
            self.assertEqual(fixture_reference(), MADDY_FIXTURE_CONFIGURATION)
        self.assertEqual([call.args[0][-1] for call in run.call_args_list],
                         [MADDY_FIXTURE_IMAGE, MADDY_FIXTURE_CONFIGURATION])

    def test_containerd_store_uses_its_approved_index_and_foreign_platforms_fail(self):
        with patch('tools.maddy_fixture.subprocess.run',
                   return_value=observed(MADDY_FIXTURE_IMAGE)):
            self.assertEqual(fixture_reference(), MADDY_FIXTURE_IMAGE)
        for image in (observed('sha256:' + 'a' * 64), observed(MADDY_FIXTURE_IMAGE, 'arm64')):
            with (self.subTest(image=image),
                  patch('tools.maddy_fixture.subprocess.run', return_value=image),
                  self.assertRaisesRegex(RuntimeError, 'differs from the approved build')):
                fixture_reference()

    def test_unavailable_image_cannot_select_a_floating_or_downloaded_replacement(self):
        with (patch('tools.maddy_fixture.subprocess.run',
                    return_value=SimpleNamespace(returncode=1, stdout='')),
              self.assertRaisesRegex(RuntimeError, 'must be loaded')):
            fixture_reference()
