import base64
import copy
import hashlib
import json
import os
import subprocess
import tarfile
import unittest
from unittest.mock import Mock, patch

from helper.maddy import LAUNCHER
from tests.qualification_support import image_files, write_archive
from tests.support import StateFixture
from tools.qualification_archive import ArtifactArchive, image_identity
from tools.qualification_deployment import running_dashboard_identity, verify_activation
from tools.qualification_installation import (
    LIBRARY,
    InstalledFile,
    InstalledTree,
    installation_manifest,
)
from tools.qualification_target import TargetConnection, negotiated_key, target_observation


class QualificationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve()

    def archive(self):
        files, identity = image_files()
        path = self.root / "image.tar"
        write_archive(path, files)
        return path, identity

    def test_oci_platform_manifest_and_configuration_are_observed_independently(self):
        path, identity = self.archive()
        observed = image_identity(path)
        self.assertEqual(observed.manifest, identity)
        self.assertNotEqual(observed.manifest, observed.configuration)
        self.assertEqual(observed.allowed_ids, {identity, observed.configuration})

    def test_reexported_single_manifest_uses_verified_configuration_when_platform_is_absent(self):
        files, identity = image_files()
        index = json.loads(files['index.json'])
        del index['manifests'][0]['platform']
        files['index.json'] = json.dumps(index).encode()
        path = self.root / 'reexported-image.tar'
        write_archive(path, files)
        self.assertEqual(image_identity(path).manifest, identity)

    def test_explicit_wrong_null_or_ambiguous_platform_is_still_rejected(self):
        for index, platform in enumerate((None, {}, {'architecture': 'arm64', 'os': 'linux'})):
            files, _ = image_files()
            manifest = json.loads(files['index.json'])
            manifest['manifests'][0]['platform'] = platform
            files['index.json'] = json.dumps(manifest).encode()
            path = self.root / ('wrong-platform-' + str(index) + '.tar')
            write_archive(path, files)
            with self.assertRaises(ValueError):
                image_identity(path)

    def test_absent_platform_does_not_bypass_configuration_architecture_or_runtime_user(self):
        for index, configuration in enumerate((
            {'architecture': 'arm64', 'os': 'linux', 'config': {'User': '10001:10001'}},
            {'architecture': 'amd64', 'os': 'linux', 'config': {'User': '0:0'}},
        )):
            content = json.dumps(configuration).encode()
            config_id = 'sha256:' + hashlib.sha256(content).hexdigest()
            manifest = json.dumps({'schemaVersion': 2, 'config': {
                'digest': config_id, 'size': len(content)}, 'layers': []}).encode()
            manifest_id = 'sha256:' + hashlib.sha256(manifest).hexdigest()
            files = {'index.json': json.dumps({'manifests': [{
                'digest': manifest_id, 'size': len(manifest)}]}).encode(),
                'blobs/sha256/' + config_id[7:]: content,
                'blobs/sha256/' + manifest_id[7:]: manifest}
            path = self.root / ('wrong-configuration-' + str(index) + '.tar')
            write_archive(path, files)
            with self.assertRaises(ValueError):
                image_identity(path)

    def test_corrupt_image_metadata_and_duplicate_archive_members_fail(self):
        files, _ = image_files()
        name = next(name for name in files if name.startswith("blobs/"))
        files[name] = b"corrupt"
        path = self.root / "corrupt.tar"
        write_archive(path, files)
        with self.assertRaises(ValueError):
            image_identity(path)
        duplicate = self.root / "duplicate.tar"
        with tarfile.open(duplicate, "x") as archive:
            archive.addfile(tarfile.TarInfo("repeated"))
            archive.addfile(tarfile.TarInfo("repeated"))
        with self.assertRaises(ValueError):
            ArtifactArchive(duplicate)

    def test_archive_links_and_paths_cannot_escape_or_replace_frozen_files(self):
        for number, name in enumerate(("/absolute", "../escape", "normalized/../escape")):
            path = self.root / f"unsafe-{number}.tar"
            write_archive(path, {name: b"fixture"})
            with self.assertRaises(ValueError):
                ArtifactArchive(path)
        path = self.root / "link.tar"
        with tarfile.open(path, "x") as archive:
            link = tarfile.TarInfo("link")
            link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(link)
        with self.assertRaises(ValueError):
            ArtifactArchive(path)

    def test_running_image_is_compared_with_archive_contents_and_replacement_is_rejected(self):
        path, identity = self.archive()
        observed = image_identity(path)
        container = {"Image": "sha256:" + "f" * 64, "State": {"Running": True},
                     "Config": {"User": "10001:10001"}}
        docker = Mock()
        docker.inspect.return_value = container
        image = {"Id": identity, "Architecture": "amd64", "Os": "linux",
                 "Config": {"User": "10001:10001"}}
        docker.run.return_value = json.dumps([image])
        self.assertEqual(running_dashboard_identity(docker, observed), container)
        docker.run.assert_called_once_with(("image", "inspect", "--platform", "linux/amd64",
                                           container["Image"]))
        for changed in ({**image, "Id": "sha256:" + "e" * 64},
                        {**image, "Architecture": "arm64"}):
            docker.run.return_value = json.dumps([changed])
            with self.assertRaises(ValueError):
                running_dashboard_identity(docker, observed)

    def installed_fixture(self):
        tree = InstalledTree(self.root)
        manifest = {}
        for target in (str(LIBRARY / "helper/service.py"),
                       "/etc/systemd/system/mail-admin-helper.service"):
            path = self.root / target.removeprefix("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"qualified fixture bytes")
            path.chmod(0o644)
            manifest[target] = InstalledFile(hashlib.sha256(path.read_bytes()).hexdigest(),
                                             0o644, os.getuid(), os.getgid())
        return tree, manifest

    def test_installed_launcher_uses_the_exact_path_invoked_by_the_real_helper(self):
        script = b'qualified renewal fixture'
        path = self.root / 'host.tar'
        write_archive(path, {'host/maddy-launcher': b'qualified launcher fixture',
                             'host/renew-cert.sh': script})
        targets = {'maddy_compose': '/fixture/maddy.yml',
                   'renewal_service': '/etc/systemd/system/maddy-cert-renew.service',
                   'renewal_timer': '/etc/systemd/system/maddy-cert-renew.timer',
                   'renewal_script': '/opt/maddy/renew-cert.sh'}
        configuration = {'inputs': {'service_gid': 10001,
                                    'backup_schedule': '*-*-* 03:00:00 Asia/Dhaka'},
                         'backup_resources': {'memory_limit_bytes': 201326592,
                                              'cpu_quota_percent': 50},
                         'consumers': {'maddy': {'compose_file': targets['maddy_compose']}},
                         'cutover': {'files': {role: {'target': target, 'mode': 0o644,
                             'sha256': hashlib.sha256(script).hexdigest()
                             if role == 'renewal_script' else 'a' * 64}
                             for role, target in targets.items()}}}
        with ArtifactArchive(path) as archive:
            manifest = installation_manifest(archive, configuration)
        self.assertEqual(manifest[LAUNCHER].sha256,
                         hashlib.sha256(b'qualified launcher fixture').hexdigest())
        self.assertEqual(manifest[LAUNCHER].mode, 0o755)

    def test_helper_or_unit_content_permissions_and_extra_source_drift_fail(self):
        tree, manifest = self.installed_fixture()
        tree.verify(manifest)
        for target in manifest:
            path = self.root / target.removeprefix("/")
            original = path.read_bytes()
            path.write_bytes(b"changed installed file")
            with self.assertRaises(ValueError):
                tree.verify(manifest)
            path.write_bytes(original)
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                tree.verify(manifest)
            path.chmod(0o644)
        extra = self.root / LIBRARY.relative_to("/") / "helper/unqualified.py"
        extra.write_bytes(b"extra module")
        with self.assertRaises(ValueError):
            tree.verify(manifest)

    def test_symlinked_installed_artifacts_fail_even_when_the_bytes_match(self):
        tree, manifest = self.installed_fixture()
        target = next(iter(manifest))
        path = self.root / target.removeprefix("/")
        retained = path.with_suffix(".retained")
        path.rename(retained)
        path.symlink_to(retained)
        with self.assertRaises(ValueError):
            tree.verify(manifest)

    def ssh_result(self):
        digest = hashlib.sha256(b"synthetic SSH public host key blob").digest()
        fingerprint = base64.b64encode(digest).decode().rstrip("=")
        metadata = {"project": "fixture-project", "zone": "fixture-zone", "instance": "123"}
        log = "debug1: Server host key: ssh-ed25519 SHA256:" + fingerprint + "\n"
        result = subprocess.CompletedProcess((), 0, json.dumps(metadata), log)
        return result, target_observation(metadata, digest.hex())

    def test_authenticated_metadata_and_negotiated_key_are_observed_not_expected_copies(self):
        result, expected = self.ssh_result()
        connection = TargetConnection("fixture@example.invalid", expected)
        with patch("tools.qualification_target.subprocess.run", return_value=result) as run:
            self.assertEqual(connection.observe(), expected)
        arguments = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", arguments)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519", arguments)
        self.assertIn("ControlPath=none", arguments)
        self.assertEqual(negotiated_key(result.stderr), expected["target_ssh_host_key_sha256"])
        for field in expected:
            wrong = {**expected, field: "d" * 64}
            with self.subTest(field=field):
                with patch("tools.qualification_target.subprocess.run", return_value=result):
                    with self.assertRaises(ValueError):
                        TargetConnection("fixture@example.invalid", wrong).observe()

    def test_missing_failed_or_ambiguous_ssh_negotiation_never_produces_target_evidence(self):
        result, expected = self.ssh_result()
        for changed in (subprocess.CompletedProcess((), 255, result.stdout, result.stderr),
                        subprocess.CompletedProcess((), 0, result.stdout, ""),
                        subprocess.CompletedProcess((), 0, result.stdout, result.stderr * 2)):
            with patch("tools.qualification_target.subprocess.run", return_value=changed):
                with self.assertRaises(ValueError):
                    TargetConnection("fixture@example.invalid", expected).observe()
        with self.assertRaises(ValueError):
            target_observation({**json.loads(result.stdout), "instance": "../wrong"}, "a" * 64)

    def test_effective_configuration_drift_is_type_sensitive(self):
        from host.preflight import _configuration_binding
        from shared.errors import ServiceError

        configuration = {"inputs": {"service_uid": 10001}, "helper_protocol": 1}
        (self.root / "deployment.json").write_text(json.dumps(configuration))
        evidence = {"artifacts": [{"id": "deployment_config", "path": "deployment.json"}]}
        _configuration_binding(configuration, (self.root, evidence))
        changed = copy.deepcopy(configuration)
        changed["helper_protocol"] = True
        with self.assertRaises(ServiceError):
            _configuration_binding(changed, (self.root, evidence))

    def generation_fixture(self, marker):
        pair = marker + b" certificate", marker + b" private key"
        identifier = hashlib.sha256(pair[0] + b"\0" + pair[1]).hexdigest()
        directory = self.root / identifier
        directory.mkdir(mode=0o750)
        for name, value in zip(("fullchain.pem", "privkey.pem"), pair, strict=True):
            (directory / name).write_bytes(value)
            (directory / name).chmod(0o640)
        record = {"active_generation": identifier, "served_sha256": "a" * 64,
                  "intent": "expected_running", "state": "active"}
        generations = Mock()
        generations.paths.directory = self.root
        generations.policy.owner, generations.policy.group = os.getuid(), os.getgid()
        generations.validator.validate.return_value = {"served_sha256": "a" * 64}
        generations.read.return_value = {
            "desired_generation": identifier, "consumers": {"mail-admin": record}
        }
        container = {"Mounts": [{"Destination": "/tls", "Source": str(directory)}]}
        return generations, container, directory

    def test_tls_observation_follows_validated_activation_after_renewal(self):
        for marker in (b"old", b"renewed"):
            generations, container, _ = self.generation_fixture(marker)
            with patch("tools.qualification_deployment.verify_served") as served:
                verify_activation(generations, {"mail-admin": container})
            served.assert_called_once_with(("127.0.0.1", 443), "a" * 64, 5)

    def test_mixed_or_changed_tls_pair_stale_mounts_and_unserved_generations_fail(self):
        generations, container, directory = self.generation_fixture(b"qualified")
        stale = {"Mounts": [{"Destination": "/tls", "Source": str(self.root / "stale")}]}
        with patch("tools.qualification_deployment.verify_served") as served:
            with self.assertRaises(ValueError):
                verify_activation(generations, {"mail-admin": stale})
        served.assert_not_called()
        with patch("tools.qualification_deployment.verify_served", side_effect=OSError("unserved")):
            with self.assertRaises(OSError):
                verify_activation(generations, {"mail-admin": container})
        (directory / "privkey.pem").write_bytes(b"changed private key")
        with patch("tools.qualification_deployment.verify_served") as served:
            with self.assertRaises(ValueError):
                verify_activation(generations, {"mail-admin": container})
        served.assert_not_called()
