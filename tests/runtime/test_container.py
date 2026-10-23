import json
import subprocess
import unittest
import uuid
from pathlib import Path

from shared.runtime_policy import RUNTIME_POLICY_NAME, RuntimePolicy
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RuntimeFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        self.authentication = RuntimeAuthentication(self, self.fixture)

    def enroll(self):
        self.authentication.enroll()

    def login(self):
        self.authentication.login()

    def test_unknown_api_returns_safe_json_over_actual_https(self):
        status, headers, body = self.fixture.api("GET", "/api/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(set(body), {"error", "request_id"})
        self.assertEqual(body["error"], "api_not_found")
        self.assertEqual(str(uuid.UUID(body["request_id"])), body["request_id"])
        self.assertEqual(headers["cache-control"], "no-store")

    def test_real_https_enrollment_cookie_helper_and_deletion_capability(self):
        self.assertEqual(self.fixture.api("GET", "/api/accounts")[0], 401)
        self.enroll()
        self.login()
        email = "runtime-fixture@rsalehin24.me"
        password = " preserved runtime fixture password "
        status, headers, result = self.fixture.api(
            "POST",
            "/api/accounts/create",
            {"request_id": str(uuid.uuid4()), "email": email, "password": password},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["state"], "complete")
        self.assertEqual(headers["cache-control"], "no-store")
        status, _, rows = self.fixture.api("GET", "/api/accounts")
        self.assertEqual(status, 200)
        self.assertEqual(rows[0]["email"], email)
        self.assertEqual(self.fixture.host.maddy.calls[0][1], password)
        self.assertNotIn(password.encode(), self.fixture.database.path.read_bytes())
        self.assertEqual(self.fixture.api("POST", "/api/logout", {})[0], 200)
        self.assertEqual(self.fixture.api("GET", "/api/accounts")[0], 401)

    def test_restarting_runtime_preserves_authentication_and_closed_bootstrap(self):
        self.enroll()
        self.login()
        self.fixture.command(("restart", self.fixture.identifier))
        self.fixture.ready()
        self.assertEqual(self.fixture.api("GET", "/api/accounts")[0], 200)
        status, _, _ = self.fixture.api("POST", "/api/setup", {"token": "invented"})
        self.assertEqual(status, 403)

    def test_read_only_authentication_database_fails_without_bypass_and_recovers(self):
        self.enroll()
        self.login()
        self.fixture.database.path.chmod(0o400)
        try:
            status, headers, _ = self.fixture.api("GET", "/api/accounts")
            self.assertEqual(status, 503)
            self.assertEqual(headers["cache-control"], "no-store")
        finally:
            self.fixture.database.path.chmod(0o600)
        self.assertEqual(self.fixture.api("GET", "/api/accounts")[0], 200)

    def test_missing_initialized_database_cannot_reopen_setup_on_restart(self):
        self.enroll()
        database = self.fixture.database.path
        original = database.with_suffix(".original")
        self.fixture.command(("stop", self.fixture.identifier))
        database.rename(original)
        self.fixture.command(("start", self.fixture.identifier))
        state = self.fixture.exited()
        self.assertFalse(state["Running"])
        self.assertNotEqual(state["ExitCode"], 0)
        self.assertFalse(database.exists())

    def test_wrong_or_missing_marker_cannot_reopen_setup_on_restart(self):
        self.enroll()
        marker = self.fixture.root / "installation.json"
        original = marker.read_bytes()
        for material in ({**json.loads(original), "installation_id": str(uuid.uuid4())}, None):
            self.fixture.command(("stop", self.fixture.identifier))
            if material is None:
                marker.rename(marker.with_suffix(".retained"))
                with self.assertRaises(subprocess.CalledProcessError) as rejected:
                    self.fixture.command(("start", self.fixture.identifier))
                self.assertIn("bind source path does not exist", rejected.exception.stderr)
                self.assertFalse(marker.exists())
            else:
                marker.write_text(json.dumps(material))
                self.fixture.command(("start", self.fixture.identifier))
            state = self.fixture.exited()
            self.assertFalse(state["Running"])
            self.assertNotEqual(state["ExitCode"], 0)
            with self.fixture.database.transaction() as connection:
                self.assertEqual(connection.execute("SELECT initialized FROM admin_auth")
                                 .fetchone()[0], 1)
            if material is None:
                marker.with_suffix(".retained").rename(marker)
            else:
                marker.write_bytes(original)
        self.fixture.command(("start", self.fixture.identifier))
        self.fixture.ready()
        self.assertEqual(self.fixture.api("POST", "/api/setup", {"token": "old"})[0], 403)

    def test_maintenance_intent_survives_restart_and_only_root_can_probe_the_stopped_database(self):
        self.enroll()
        self.login()
        policy = RuntimePolicy(
            self.fixture.root / "installation.json",
            self.fixture.root / "keys" / RUNTIME_POLICY_NAME,
        )
        policy.pause()
        self.fixture.command(("restart", self.fixture.identifier))
        self.assertNotEqual(self.fixture.exited()["ExitCode"], 0)
        logs = self.fixture.command(("logs", self.fixture.identifier))
        self.assertIn("operator_maintenance", logs.stdout + logs.stderr)
        arguments = self.fixture.probe_arguments()
        try:
            result = self.fixture.command(arguments)
        except subprocess.CalledProcessError as error:
            self.fail("Root database probe failed: " + error.stderr)
        self.assertEqual(result.returncode, 0)
        position = arguments.index("--user") + 1
        unprivileged = arguments[:position] + ("10001:10001",) + arguments[position + 1 :]
        with self.assertRaises(subprocess.CalledProcessError) as rejected:
            self.fixture.command(unprivileged)
        self.assertIn("root_required", rejected.exception.stderr)
        policy.resume()
        self.fixture.command(("start", self.fixture.identifier))
        self.fixture.ready()
        self.assertEqual(self.fixture.api("GET", "/api/accounts")[0], 200)

    def test_runtime_privileges_mounts_image_tools_and_measured_container_memory(self):
        source = (
            "import os,json,shutil,pathlib\n"
            "cap=next(x for x in open('/proc/self/status') if x.startswith('CapEff:')).strip()\n"
            "p=pathlib.Path('/sys/fs/cgroup/memory.peak')\n"
            "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'capabilities':cap,"
            "'docker':shutil.which('docker'),'node':shutil.which('node'),"
            "'key_readable':os.access('/tls/privkey.pem',os.R_OK),"
            "'maddy_data_present':pathlib.Path('/opt/maddy/data').exists(),"
            "'memory_peak_bytes':int(p.read_text())}))\n"
        )
        self.enroll()
        self.login()
        result = json.loads(
            self.fixture.command(("exec", self.fixture.identifier, "python", "-c", source)).stdout
        )
        self.assertEqual((result["uid"], result["gid"]), (10001, 10001))
        self.assertEqual(int(result["capabilities"].split()[1], 16), 0)
        self.assertIsNone(result["docker"])
        self.assertIsNone(result["node"])
        self.assertFalse(result["maddy_data_present"])
        self.assertTrue(result["key_readable"])
        self.assertLess(result["memory_peak_bytes"], 128 * 1024 * 1024)
        result["scope"] = "isolated web container only; not production host peak"
        Path("build/runtime-observation.json").write_text(json.dumps(result, indent=2) + "\n")
