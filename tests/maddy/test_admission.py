import copy
import json
import os
import tarfile
import unittest
import uuid
from unittest.mock import patch

from helper.maddy import DockerMaddy
from host.docker import Docker
from host.preflight import validate_image
from shared.errors import ServiceError
from tests.maddy_support import RealMaddyFixture
from tests.runtime_compose_support import displaced_source


class MaddyAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        self.fixture.maddy.validate()

    def assert_rejected_before_cli(self, code):
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as command:
            with self.assertRaisesRegex(ServiceError, code):
                self.fixture.maddy.validate()
        command.assert_not_called()

    def test_changed_mounts_and_missing_or_wrong_type_databases_fail_without_initialization(self):
        deployment = self.fixture.maddy.deployment
        mounts = copy.deepcopy(deployment['maddy_mounts'])
        deployment['maddy_mounts']['/data'] = str(self.fixture.root / 'unexpected-data')
        try:
            self.assert_rejected_before_cli('maddy_mount_mismatch')
        finally:
            deployment['maddy_mounts'] = mounts
        for name in ('credentials.db', 'imapsql.db'):
            path = self.fixture.data / name
            with self.subTest(database=name), displaced_source(path):
                self.assert_rejected_before_cli('maddy_storage_missing')
                self.assertFalse(path.exists())
                path.mkdir()
                self.assert_rejected_before_cli('maddy_storage_missing')
                self.assertTrue(path.is_dir())
        self.fixture.maddy.validate()
        self.assertEqual(self.fixture.maddy.inventories(), {'credentials': [], 'storage': []})

    def test_real_other_image_identity_is_rejected_before_any_maddy_cli(self):
        deployment = self.fixture.maddy.deployment
        original = deployment['inputs']['maddy_image_id']
        other = os.environ['MAIL_ADMIN_RUNTIME_IMAGE']
        self.assertNotEqual(other, original)
        self.fixture.helper_docker((self.fixture.container, other))
        deployment['inputs']['maddy_image_id'] = other
        try:
            self.assert_rejected_before_cli('maddy_identity_mismatch')
        finally:
            deployment['inputs']['maddy_image_id'] = original
        self.fixture.maddy.validate()

    def test_actual_wrong_architecture_image_metadata_fails_both_host_admissions(self):
        archive = self.fixture.root / 'empty-rootfs.tar'
        with tarfile.open(archive, 'w'):
            pass
        image = self.fixture.command(('image', 'import', '--platform', 'linux/arm64',
                                      str(archive))).stdout.strip()
        self.addCleanup(self.fixture.command, ('image', 'rm', image))
        observed = json.loads(self.fixture.command(('image', 'inspect', image)).stdout)[0]
        self.assertEqual(observed['Architecture'], 'arm64')
        name = 'mail-admin-fixture-' + str(uuid.uuid4())
        arguments = ('create', '--name', name, '--platform', 'linux/arm64',
                     '--label', 'mail-admin.fixture=true', '--memory', '64m',
                     '--memory-swap', '64m', '--cpus', '0.25', '--pids-limit', '32',
                     '--network', 'none', '--read-only', '--cap-drop', 'ALL')
        identifier = self.fixture.command(arguments + (image, '/fixture-never-executed'))
        identifier = identifier.stdout.strip()
        self.addCleanup(self.fixture.command, ('rm', identifier))
        adapter = self.fixture.helper_docker((name, image))
        deployment = copy.deepcopy(self.fixture.maddy.deployment)
        deployment['inputs']['maddy_image_id'] = image
        helper = DockerMaddy(deployment, adapter)
        with patch.object(helper, 'change', wraps=helper.change) as command:
            with self.assertRaisesRegex(ServiceError, 'maddy_identity_mismatch'):
                helper.validate()
        command.assert_not_called()
        with self.assertRaisesRegex(ServiceError, 'dashboard_architecture_mismatch'):
            validate_image({'dashboard_image_id': image}, Docker(self.fixture.docker))
        state = json.loads(self.fixture.command(('inspect', identifier)).stdout)[0]['State']
        self.assertEqual(state['Status'], 'created')
        self.assertFalse(state['Running'])
