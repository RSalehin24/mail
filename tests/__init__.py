"""Deterministic application tests and isolated qualification fixtures."""

import os
from pathlib import Path

# Fixture values are explicitly confined to the test harness.
os.environ.setdefault("MAIL_CONFIG", str(Path(__file__).with_name("site.json")))
