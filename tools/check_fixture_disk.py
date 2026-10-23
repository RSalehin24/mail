"""Reject local image builds before exhausting a sparse engine's host filesystem."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.resources import require_fixture_disk  # noqa: E402

if __name__ == '__main__':
    require_fixture_disk(ROOT)
