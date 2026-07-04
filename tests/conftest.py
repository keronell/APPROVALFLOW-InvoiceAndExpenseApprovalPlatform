"""Shared test fixtures, visible to both unit and integration tests."""

import json
from pathlib import Path

import pytest

FIXTURES_PATH = Path(__file__).parents[1] / "Examples" / "sample-invoices.json"


@pytest.fixture(scope="session")
def fixtures_doc() -> dict:
    return json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixtures_by_id(fixtures_doc: dict) -> dict[str, dict]:
    return {f["id"]: f for f in fixtures_doc["fixtures"]}


@pytest.fixture
def anyio_backend() -> str:
    """Run @pytest.mark.anyio tests on asyncio only."""
    return "asyncio"
