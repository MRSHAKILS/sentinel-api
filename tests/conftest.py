import json
import sys
from pathlib import Path

import pytest

# Make the project root importable when running `pytest` from anywhere.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SAMPLE_FILE = Path(__file__).parent / "data" / "sample_cases.json"


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture(scope="session")
def sample_cases():
    data = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    return data["cases"]
