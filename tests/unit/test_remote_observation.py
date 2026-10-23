import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.run_remote import (
    NativeRun,
    fixture_support_files,
    live_observation,
    local_archive,
    verify_images,
)


class RemoteObservationTests(unittest.TestCase):
    def test_transferred_source_is_required_when_the_host_driver_has_no_checkout(self):
        from tools.run_linux import transferred_source

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                transferred_source(root)
            (root / 'images.json').write_text(json.dumps({'source_sha256': 'a' * 64}))
            self.assertEqual(transferred_source(root), 'a' * 64)
            (root / 'images.json').write_text(json.dumps({'source_sha256': None}))
            with self.assertRaises(ValueError):
                transferred_source(root)

    def test_transferred_source_validation_cannot_be_replaced_by_a_missing_local_checkout(self):
        images = {'mail-admin:candidate': 'runtime', 'mail-admin:qualification': 'qualification'}
        values = [{'runtime_sha256': 'matching', 'source_sha256': None},
                  {'runtime_sha256': 'matching', 'source_sha256': 'a' * 64}]
        with (patch('tools.run_remote.image_observation', side_effect=values),
              patch('tools.run_remote.source_digest') as local):
            verify_images(images, 'a' * 64)
        local.assert_not_called()
        with self.assertRaises(ValueError):
            verify_images(images, 'invalid')
    def test_packaged_host_driver_starts_without_the_local_checkout_or_python_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            for source in fixture_support_files():
                shutil.copy2(source, root / "tools" / source.name)
            result = subprocess.run(
                (sys.executable, str(root / "tools/run_linux.py"), "--help"),
                cwd=root,
                env={**os.environ, "PYTHONPATH": ""},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--suite", result.stdout)

    def record(self):
        return {
            "Id": "live-fixture-id",
            "Image": "fixture-image",
            "Config": {"Env": ["PRIVATE=synthetic"]},
            "HostConfig": {"ReadonlyRootfs": True},
            "Mounts": [
                {"Destination": "/data", "Source": "/fixture/data", "RW": True},
                {"Destination": "/tls", "Source": "/fixture/tls", "RW": False},
            ],
            "State": {"StartedAt": "unchanged", "Running": True},
        }

    def test_mount_order_is_irrelevant_and_private_configuration_is_not_reported(self):
        original = self.record()
        reordered = copy.deepcopy(original)
        reordered["Mounts"].reverse()
        self.assertEqual(live_observation(original), live_observation(reordered))
        self.assertNotIn("PRIVATE", str(live_observation(original)))

    def test_changed_mount_image_start_time_or_configuration_is_detected(self):
        original = self.record()
        for field, value in (
            ("Image", "replacement"),
            ("Config", {}),
            ("Mounts", []),
            ("State", {"StartedAt": "new", "Running": True}),
        ):
            changed = {**original, field: value}
            self.assertNotEqual(live_observation(original), live_observation(changed))

    def test_stale_source_and_changed_runtime_bytes_cannot_be_sent_for_native_tests(self):
        images = {"mail-admin:candidate": "runtime", "mail-admin:qualification": "qualification"}
        runtime = {"runtime_sha256": "matching", "source_sha256": None}
        for qualification in (
            {"runtime_sha256": "matching", "source_sha256": "old"},
            {"runtime_sha256": "different", "source_sha256": "current"},
        ):
            with (
                patch("tools.run_remote.source_digest", return_value="current"),
                patch(
                    "tools.run_remote.command",
                    side_effect=[json.dumps(runtime), json.dumps(qualification)],
                ),
            ):
                with self.assertRaises(RuntimeError):
                    verify_images(images)

    def test_concurrent_rebuild_during_export_fails_before_upload(self):
        with (
            patch(
                "tools.run_remote.image_snapshot", side_effect=[{"image": "old"}, {"image": "new"}]
            ),
            patch("tools.run_remote.source_digest", return_value="current"),
            patch("tools.run_remote.verify_images"),
            patch("tools.run_remote.command"),
        ):
            with self.assertRaisesRegex(RuntimeError, "changed during export"):
                local_archive(Path("/unused/fixture.tar.gz"))

    def test_timeout_or_launch_error_cannot_be_reported_as_a_passing_native_run(self):
        for error in (subprocess.TimeoutExpired("fixture SSH", 900), OSError("fixture SSH failed")):
            with tempfile.TemporaryDirectory() as directory:
                (Path(directory) / "images.json").write_text(json.dumps({
                    "runtime_modules": ["test_first", "test_second"],
                }))
                native = NativeRun("fixture@192.0.2.1", (Path(directory), "/fixture"))
                native.outcomes.update({suite: "pass" for suite in native.outcomes})
                native.collected = True
                with patch("tools.run_remote.subprocess.run", side_effect=error):
                    with self.assertRaises(type(error)):
                        native.execute("runtime")
                self.assertEqual(native.outcomes["runtime"], "unknown")
                self.assertEqual(native.result(True), "fail")
                self.assertEqual(native.failures, ["runtime"])

    def test_missing_report_transfer_blocks_success_even_after_every_suite_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'images.json').write_text(json.dumps({
                'maddy_modules': ['test_first', 'test_second'],
                'runtime_modules': ['test_runtime'],
            }))
            native = NativeRun("fixture@192.0.2.1", (Path(directory), "/fixture"))
            completed = subprocess.CompletedProcess((), 0, "synthetic assertions passed", "")
            with patch("tools.run_remote.subprocess.run", return_value=completed):
                for suite in native.outcomes:
                    native.execute(suite)
            with (
                patch("tools.run_remote.ssh"),
                patch("tools.run_remote.command", side_effect=OSError("fixture transfer failed")),
            ):
                with self.assertRaises(OSError):
                    native.collect()
            self.assertFalse(native.collected)
            self.assertEqual(native.result(True), "fail")

    def test_ssh_supervisor_covers_each_unchanged_module_deadline_and_rejects_bad_inventory(self):
        from tools.maddy_modules import MODULE_TIMEOUT_SECONDS

        with tempfile.TemporaryDirectory() as directory:
            native = NativeRun('fixture@192.0.2.1', (Path(directory), '/fixture'))
            path = Path(directory) / 'images.json'
            path.write_text(json.dumps({
                'maddy_modules': ['test_first', 'test_second'],
                'runtime_modules': ['test_first', 'test_second', 'test_third'],
            }))
            self.assertEqual(MODULE_TIMEOUT_SECONDS, 900)
            self.assertEqual(native._timeout('maddy'), 2 * 900 + 60)
            self.assertEqual(native._timeout('runtime'), 3 * 900 + 60)
            for modules in ([], ['test_first', 'test_first'], [['invalid']], ['../outside']):
                for suite in ('maddy', 'runtime'):
                    path.write_text(json.dumps({suite + '_modules': modules}))
                    with self.assertRaises(ValueError):
                        native._timeout(suite)

    def test_timeout_preserves_actual_captured_output_without_inventing_suite_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'images.json').write_text(json.dumps({
                'runtime_modules': ['test_runtime'],
            }))
            native = NativeRun('fixture@192.0.2.1', (Path(directory), '/fixture'))
            error = subprocess.TimeoutExpired('fixture SSH', 900,
                                               output=b'actual completed assertion\n',
                                               stderr='actual incomplete assertion\n')
            with patch('tools.run_remote.subprocess.run', side_effect=error):
                with self.assertRaises(subprocess.TimeoutExpired):
                    native.execute('runtime')
            self.assertEqual((Path(directory) / 'runtime.log').read_text(),
                             'actual completed assertion\nactual incomplete assertion\n')
            self.assertEqual(native.outcomes['runtime'], 'unknown')
            self.assertEqual(native.result(True), 'fail')
