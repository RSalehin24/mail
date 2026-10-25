import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.https_browser_fixture import publish_material


class BrowserMaterialTests(unittest.TestCase):
    def test_private_material_has_runner_ownership_before_ready_filename_is_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            owner = directory.stat()

            def transfer(path, uid, gid):
                self.assertEqual((uid, gid), (owner.st_uid, owner.st_gid))
                self.assertEqual(path.name, 'material.pending.json')
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertFalse((directory / 'material.json').exists())

            with patch('tools.https_browser_fixture.os.chown', side_effect=transfer):
                publish_material(directory, {'password': 'synthetic-private-input'})
            ready = directory / 'material.json'
            self.assertEqual(stat.S_IMODE(ready.stat().st_mode), 0o600)
            self.assertEqual(json.loads(ready.read_text()), {'password': 'synthetic-private-input'})
            self.assertFalse((directory / 'material.pending.json').exists())

    def test_failed_ownership_transfer_cannot_publish_ready_material(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with (patch('tools.https_browser_fixture.os.chown', side_effect=PermissionError),
                  self.assertRaises(PermissionError)):
                publish_material(directory, {'password': 'synthetic-private-input'})
            self.assertFalse((directory / 'material.json').exists())
