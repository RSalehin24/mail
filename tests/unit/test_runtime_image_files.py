import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY_MODULE = "backend.local_host"
COPIED_PACKAGES = ("helper", "host", "shared")


def runtime_stage_copies():
    """Source paths the Dockerfile's runtime stage copies, directories expanded to files."""
    stage = re.search(r"AS runtime\n(.*?)\nFROM ", (ROOT / "Dockerfile").read_text(), re.S)[1]
    copied = set()
    for line in re.sub(r"\\\n\s*", " ", stage).splitlines():
        words = line.split()
        if words[:1] != ["COPY"] or words[1].startswith("--from"):
            continue
        for source in words[1:-1]:
            path = ROOT / source
            found = path.rglob("*.py") if path.is_dir() else [path]
            copied |= {item.relative_to(ROOT) for item in found}
    return copied


def module_file(name):
    path = ROOT / (name.replace(".", "/") + ".py")
    return path if path.is_file() else ROOT / name.replace(".", "/") / "__init__.py"


def imported_names(source):
    for node in ast.walk(ast.parse(source.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def import_closure(entry):
    seen, pending = set(), [entry]
    while pending:
        name = pending.pop()
        source = module_file(name)
        if name in seen or not source.is_file():
            continue
        seen.add(name)
        parts = name.split(".")
        pending += [".".join(parts[:depth]) for depth in range(1, len(parts))]
        pending += imported_names(source)
    return seen


class RuntimeImageFileTests(unittest.TestCase):
    def test_runtime_image_holds_every_module_the_in_process_host_imports(self):
        copied = runtime_stage_copies()
        names = [name for name in import_closure(ENTRY_MODULE)
                 if name.split(".")[0] in COPIED_PACKAGES]
        self.assertGreater(len(names), 10)
        for name in names:
            self.assertIn(module_file(name).relative_to(ROOT), copied, name)
