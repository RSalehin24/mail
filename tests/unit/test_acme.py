import copy
import unittest

from host.container_policy import TEMPORARY_PATH, ContainerPolicy
from host.image_tools import ImageTools
from host.renewal import Certbot
from shared.errors import ServiceError


class GenerationFixture:
    def __init__(self):
        self.record = {}
        self.interrupt_starting_record = False

    def read(self):
        return copy.deepcopy(self.record)

    def write(self, record):
        if self.interrupt_starting_record and record["acme_run"]["state"] == "starting":
            raise OSError("synthetic process death after Docker create")
        self.record = copy.deepcopy(record)


class AcmeDocker:
    def __init__(self):
        self.calls = []
        self.containers = {}
        self.surviving_child = False
        self.exit_code = 0
        self.count = 0

    def run(self, arguments):
        self.calls.append(arguments)
        if arguments[0] == "create":
            return self.create(arguments)
        if arguments[:2] == ("container", "ls"):
            name = arguments[arguments.index("--filter") + 1].removeprefix("name=^/")[:-1]
            return "\n".join(
                key for key, value in self.containers.items() if value["Name"] == "/" + name
            )
        if arguments[0] == "rm":
            assert not self.containers[arguments[1]]["State"]["Running"]
            del self.containers[arguments[1]]
            return ""
        if arguments[:2] == ("start", "--attach"):
            self.start(arguments[2])
            return ""
        raise AssertionError(arguments)

    def create(self, arguments):
        self.count += 1
        identifier = f"{self.count:064x}"
        name = arguments[arguments.index("--name") + 1]
        self.containers[identifier] = {
            "Id": identifier, "Name": "/" + name,
            "Config": {"Labels": {"mail-admin.certbot-run": name}},
            "State": {"Status": "created", "Running": False, "ExitCode": 0},
        }
        return identifier

    def start(self, identifier):
        self.containers[identifier]["State"] = {
            "Status": "running" if self.surviving_child else "exited",
            "Running": self.surviving_child, "ExitCode": self.exit_code,
        }
        if self.surviving_child:
            raise ServiceError("synthetic_lost_attach", 503)

    def inspect(self, identifier):
        return copy.deepcopy(self.containers[identifier])


class AcmeTests(unittest.TestCase):
    def setUp(self):
        self.generations, self.docker = GenerationFixture(), AcmeDocker()
        self.configuration = {
            "certbot_image": "sha256:" + "a" * 64,
            "transient_resources": {
                "memory_limit_bytes": 64 * 1024 * 1024,
                "tmpfs_limit_bytes": 1024 * 1024,
                "cpu_limit": 0.5,
            },
        }
        self.certbot = Certbot(self.docker, (self.generations, self.configuration))

    def test_acme_and_image_utilities_share_explicit_limits_and_finite_logs(self):
        self.certbot.acquire()
        arguments = self.docker.calls[0]
        self.assertEqual(arguments[0], "create")
        self.assertEqual(arguments[arguments.index("--memory") + 1], str(64 * 1024 * 1024))
        self.assertEqual(arguments[arguments.index("--memory-swap") + 1], str(64 * 1024 * 1024))
        self.assertIn("max-file=2", arguments)
        self.assertIn("max-size=5m", arguments)
        self.assertIn("/var/log/letsencrypt:size=1048576,mode=1777", arguments)
        self.assertEqual(arguments[arguments.index("--pull") + 1], "never")
        image_arguments = ImageTools(self.docker, self.configuration).base()
        self.assertIn("max-file=2", image_arguments)
        self.assertIn(TEMPORARY_PATH + ":size=1048576,mode=1777", image_arguments)

    def test_next_run_removes_only_the_previous_completed_container(self):
        self.certbot.acquire()
        previous = self.generations.read()["acme_run"]["container_id"]
        self.certbot.acquire("dry-run")
        self.assertNotIn(previous, self.docker.containers)
        self.assertEqual(len(self.docker.containers), 1)
        self.assertEqual(self.docker.calls.count(("rm", previous)), 1)
        creates = [call for call in self.docker.calls if call[0] == "create"]
        self.assertNotIn("--dry-run", creates[0])
        self.assertIn("--dry-run", creates[1])

    def test_interrupted_create_is_found_by_durable_name_and_never_started(self):
        self.generations.interrupt_starting_record = True
        with self.assertRaises(OSError):
            self.certbot.acquire()
        self.assertEqual(self.generations.read()["acme_run"]["state"], "creating")
        self.assertEqual(len(self.docker.containers), 1)
        self.certbot.reconcile()
        self.assertEqual(self.generations.read()["acme_run"]["state"], "not_started")
        self.assertEqual(self.docker.containers, {})
        self.assertFalse(any(call[0] == "start" for call in self.docker.calls))

    def test_surviving_acme_child_blocks_new_acquisition_until_exit_is_observed(self):
        self.docker.surviving_child = True
        with self.assertRaisesRegex(ServiceError, "acme_completion_unknown"):
            self.certbot.acquire()
        self.assertEqual(self.generations.read()["acme_run"]["state"], "unknown")
        with self.assertRaisesRegex(ServiceError, "acme_completion_unknown"):
            self.certbot.acquire()
        self.assertEqual(self.docker.count, 1)
        previous = self.generations.read()["acme_run"]["container_id"]
        self.docker.containers[previous]["State"].update({"Status": "exited", "Running": False})
        self.certbot.reconcile()
        self.assertEqual(self.generations.read()["acme_run"]["state"], "completed")

    def test_failed_acme_exit_is_recorded_and_does_not_publish_a_generation(self):
        self.docker.exit_code = 17
        with self.assertRaisesRegex(ServiceError, "acme_renewal_failed"):
            self.certbot.acquire()
        self.assertEqual(self.generations.read()["acme_run"]["exit_code"], 17)
        self.assertNotIn("desired_generation", self.generations.read())

    def test_foreign_container_label_prevents_cleanup(self):
        self.certbot.acquire()
        previous = self.generations.read()["acme_run"]["container_id"]
        self.docker.containers[previous]["Config"]["Labels"] = {"foreign": "true"}
        with self.assertRaisesRegex(ServiceError, "untrusted_operation_container"):
            self.certbot.acquire()
        self.assertIn(previous, self.docker.containers)
        self.assertEqual(self.docker.count, 1)

    def test_interruption_after_completed_container_removal_is_recoverable(self):
        self.certbot.acquire()
        previous = self.generations.read()["acme_run"]["container_id"]
        self.docker.run(("rm", previous))
        self.certbot.acquire()
        self.assertEqual(self.docker.count, 2)
        self.assertEqual(len(self.docker.containers), 1)

    def test_unmeasured_or_invalid_resources_cannot_start_or_remove_a_container(self):
        original = self.configuration["transient_resources"]
        invalid_resources = (
            None, {}, {**original, "memory_limit_bytes": True},
            {**original, "cpu_limit": float("nan")},
        )
        for resources in invalid_resources:
            with self.subTest(resources=resources):
                self.configuration["transient_resources"] = resources
                with self.assertRaisesRegex(ServiceError, "measured_transient_resources_required"):
                    self.certbot.acquire()
                self.assertEqual(self.docker.calls, [])

    def test_all_acme_temporary_filesystems_fit_inside_the_cgroup_limit(self):
        self.configuration["transient_resources"]["tmpfs_limit_bytes"] = 40 * 1024 * 1024
        policy = ContainerPolicy.load(self.configuration)
        with self.assertRaisesRegex(ServiceError, "transient_temporary_budget_exceeds_memory"):
            policy.arguments((TEMPORARY_PATH, "/var/log/letsencrypt"))
