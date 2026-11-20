"""The runtime image holds only what the dashboard runs: no docs, tests, tools or Docker client."""

import ast
import json
import os
import subprocess
import unittest
from pathlib import Path

from helper.recovery import QUALIFIED_FILES

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_ENTRIES = {"backend", "helper", "host", "shared", "static"}
REPOSITORY_PACKAGES = ("backend", "helper", "host", "shared")
# Names that exist in the repository for development and operations, never at runtime.
DEVELOPMENT_NAMES = {
    "tests", "tools", "docs", "planning", "verification", "frontend", "Dockerfile",
    "docker-compose.yml", "Makefile", "pyproject.toml", "README.md", ".git",
}
# The recovery qualification proof hashes these worker files, so the dashboard must hold them.
QUALIFIED_HOST_FILES = {Path(name).name for name in QUALIFIED_FILES if name.startswith("host/")}
IMAGE_LISTING = """
import json, os, shutil
entries = [os.path.relpath(os.path.join(base, name), "/app")
           for base, directories, files in os.walk("/app") for name in directories + files]
print(json.dumps({"entries": entries, "docker": shutil.which("docker")}))
"""


def module_path(module):
    base = ROOT / Path(*module.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def imported_repository_modules(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        found.update(name for name in names
                     if name.split(".")[0] in REPOSITORY_PACKAGES and module_path(name).is_file())
    return found


def import_closure(roots):
    seen, pending = set(), list(roots)
    while pending:
        module = pending.pop()
        if module not in seen:
            seen.add(module)
            pending.extend(imported_repository_modules(module_path(module)))
    return seen


def application_modules():
    return {
        ".".join(path.relative_to(ROOT).with_suffix("").parts)
        for path in (ROOT / "backend").rglob("*.py")
    }


class RuntimeImageContentsTests(unittest.TestCase):
    def setUp(self):
        result = subprocess.run(
            ("docker", "run", "--rm", "--pull", "never", "--platform", "linux/amd64",
             "--network", "none", "--read-only", "--cap-drop", "ALL", "--entrypoint", "python",
             os.environ["MAIL_ADMIN_RUNTIME_REFERENCE"], "-c", IMAGE_LISTING),
            capture_output=True, text=True, timeout=60, check=True,
        )
        self.image = json.loads(result.stdout)

    def test_only_application_directories_are_present(self):
        top_level = {entry.split("/")[0] for entry in self.image["entries"]}
        self.assertEqual(top_level, APPLICATION_ENTRIES)

    def test_no_development_files_are_present(self):
        parts = {part for entry in self.image["entries"] for part in Path(entry).parts}
        self.assertEqual(parts & DEVELOPMENT_NAMES, set())
        self.assertEqual([entry for entry in self.image["entries"] if entry.endswith(".md")], [])

    def test_host_contains_only_modules_the_dashboard_imports(self):
        needed = {
            module.split(".")[1] + ".py" if module != "host" else "__init__.py"
            for module in import_closure(application_modules()) if module.split(".")[0] == "host"
        } | {"__init__.py"} | QUALIFIED_HOST_FILES
        present = {Path(entry).name for entry in self.image["entries"]
                   if entry.startswith("host/") and entry.endswith(".py")}
        self.assertEqual(present, needed)

    def test_the_image_has_no_docker_client(self):
        self.assertIsNone(self.image["docker"])
