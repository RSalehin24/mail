import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support import StateFixture
from tools.maddy_modules import aggregate_modules, module_inventory
from tools.run_tests import report_directory, test_selection


class MaddyModuleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.root = self.fixture.root.resolve()
        self.inventory = {"source_sha256": "a" * 64, "modules": ["test_first", "test_second"]}
        self.template = {
            "suite": "maddy", "source_sha256": "a" * 64, "result": "pass",
            "python": "3.11.17", "system": "Linux", "machine": "x86_64",
            "qualification_image": "sha256:" + "b" * 64, "runtime_image": "sha256:" + "c" * 64,
            "docker_engine_architecture": "aarch64", "controller_memory": {
                "state": "observed", "limit_bytes": 192 * 1024**2, "peak_bytes": 100 * 1024**2,
            },
        }
        for name in self.inventory["modules"]:
            (self.root / name).mkdir(mode=0o700)
            self.write(name, {**self.template, "module": name, "tests": {name + ".case": "pass"}})

    def write(self, name, report):
        (self.root / name / "maddy-checks.json").write_text(json.dumps(report))

    def aggregate(self, outcomes=None):
        return aggregate_modules(self.root, (
            self.inventory, outcomes or {name: 0 for name in self.inventory["modules"]},
            "2026-01-01T00:00:00+00:00",
        ))

    def test_all_modules_are_required_and_original_reports_remain_intact(self):
        before = {p: p.read_bytes() for p in self.root.rglob("maddy-checks.json")}
        report = self.aggregate()
        self.assertEqual(report["result"], "pass")
        self.assertEqual(set(report["tests"]), {"test_first.case", "test_second.case"})
        self.assertEqual(report["controller_memory"]["peak_bytes"], 100 * 1024**2)
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(set(report["module_reports"]), set(self.inventory["modules"]))

    def test_killed_controller_or_missing_result_cannot_be_replaced_by_other_passes(self):
        self.assertEqual(self.aggregate({"test_first": 0, "test_second": 137})["result"], "fail")
        self.inventory["modules"].append("test_missing")
        self.assertEqual(self.aggregate()["result"], "fail")
        self.assertEqual(self.aggregate({"test_first": 0})["result"], "fail")

    def test_source_image_limit_skip_and_duplicate_outcomes_all_block_aggregation(self):
        for change in (
            {"source_sha256": "d" * 64}, {"runtime_image": "sha256:" + "e" * 64},
            {"tests": {"test_second.case": "skipped"}},
            {"tests": {"test_first.case": "pass"}},
            {"controller_memory": {"state": "observed", "limit_bytes": 256 * 1024**2,
                                   "peak_bytes": 100 * 1024**2}},
        ):
            with self.subTest(change=change):
                self.write("test_second", {**copy.deepcopy(self.template),
                    "module": "test_second", "tests": {"test_second.case": "pass"}, **change})
                self.assertEqual(self.aggregate()["result"], "fail")

    def test_malformed_report_or_invalid_inventory_is_an_explicit_failure(self):
        self.write("test_second", {"tests": "invalid"})
        self.assertEqual(self.aggregate()["result"], "fail")
        for names in ([], ["test_duplicate", "test_duplicate"], ["../outside"], ["test[pattern]"]):
            with self.subTest(names=names):
                with self.assertRaises(ValueError):
                    module_inventory("pinned-image", lambda _, selected=names: json.dumps({
                        "modules": selected,
                    }))

    def test_partial_selection_cannot_overwrite_the_full_suite_report(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            report_directory(None, "test_first")
        for suite, module in (("unit", "test_runner"), ("maddy", "../outside"),
                              ("maddy", "test_missing_module")):
            with self.subTest(suite=suite, module=module):
                with self.assertRaises(ValueError):
                    test_selection(suite, module)
        with patch("tools.run_tests.ROOT", self.root):
            selected = report_directory(Path("build") / "private-module", "test_first")
            self.assertEqual(selected, self.root / "build/private-module")
            with self.assertRaises(ValueError):
                report_directory(Path("build"), "test_first")
            with self.assertRaises(ValueError):
                report_directory(Path("../outside"), "test_first")
