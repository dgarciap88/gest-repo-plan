import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "plans" / "examples" / "implement-task.json"


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def example_data() -> dict:
    """Copia editable del plan de ejemplo."""
    return copy.deepcopy(json.loads(EXAMPLE.read_text(encoding="utf-8")))
