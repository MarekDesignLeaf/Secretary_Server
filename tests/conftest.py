"""
Test configuration for Secretary CRM server tests.

Run tests with:
    JWT_SECRET=test-secret python -m pytest tests/ -v -p no:cacheprovider

Or on Windows:
    set JWT_SECRET=test-secret && python -m pytest tests/ -v -p no:cacheprovider
"""

from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _isolate_from_production_db(monkeypatch):
    # A developer machine may carry DATABASE_URL pointing at the Railway
    # production database; tests must always run on the in-memory repository.
    monkeypatch.delenv("DATABASE_URL", raising=False)
