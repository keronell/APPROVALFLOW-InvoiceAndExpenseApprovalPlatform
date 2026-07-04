"""Config service HTTP surface with an in-memory Dapr state fake."""

import pytest
from approvalflow_config.app import build_app
from approvalflow_config.defaults import DEFAULT_THRESHOLDS
from fastapi.testclient import TestClient


class FakeDapr:
    """Stand-in for DaprIO backed by a dict (no sidecar needed)."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    async def get_state(self, store: str, key: str):
        return self._state.get(f"{store}::{key}")

    async def save_state(self, store: str, key: str, value: object) -> None:
        self._state[f"{store}::{key}"] = value


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(FakeDapr()))


def test_defaults_served_before_any_override(client):
    resp = client.get("/config/thresholds")
    assert resp.status_code == 200
    assert resp.json()["ceiling_usd"] == DEFAULT_THRESHOLDS["ceiling_usd"] == "250"


def test_put_overrides_thresholds_without_redeploy(client):
    put = client.put("/config/thresholds", json={"ceiling_usd": "500"})
    assert put.status_code == 200
    assert put.json()["ceiling_usd"] == "500"
    # Persisted: a subsequent GET reflects the change.
    assert client.get("/config/thresholds").json()["ceiling_usd"] == "500"


def test_policy_endpoint(client):
    assert "summary" in client.get("/config/policy").json()
