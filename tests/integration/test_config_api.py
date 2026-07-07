"""Config service: defaults, protected + validated runtime overrides (F7/M13)."""

import pytest
from approvalflow_config.app import build_app
from approvalflow_config.defaults import DEFAULT_THRESHOLDS
from fastapi.testclient import TestClient

TOKEN = "test-config-token"
AUTH = {"X-Config-Token": TOKEN}


class FakeDapr:
    """Stand-in for DaprIO backed by a dict (no sidecar needed)."""

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    async def get_state(self, store: str, key: str):
        return self._state.get(f"{store}::{key}")

    async def save_state(self, store: str, key: str, value: object) -> None:
        self._state[f"{store}::{key}"] = value


@pytest.fixture
def client(monkeypatch) -> TestClient:
    monkeypatch.setenv("CONFIG_API_TOKEN", TOKEN)
    return TestClient(build_app(FakeDapr()))


def test_defaults_served_before_any_override(client):
    resp = client.get("/config/thresholds")
    assert resp.status_code == 200
    assert resp.json()["ceiling_usd"] == DEFAULT_THRESHOLDS["ceiling_usd"] == "250"


def test_put_overrides_thresholds_without_redeploy(client):
    put = client.put("/config/thresholds", json={"ceiling_usd": "500"}, headers=AUTH)
    assert put.status_code == 200
    assert put.json()["ceiling_usd"] == "500"
    # Persisted: a subsequent GET reflects the change.
    assert client.get("/config/thresholds").json()["ceiling_usd"] == "500"


def test_mutation_requires_the_controller_token(client):
    assert client.put("/config/thresholds", json={"ceiling_usd": "500"}).status_code == 401
    wrong = client.put(
        "/config/thresholds", json={"ceiling_usd": "500"},
        headers={"X-Config-Token": "nope"},
    )
    assert wrong.status_code == 401
    # The posture is unchanged after the rejected attempts.
    assert client.get("/config/thresholds").json()["ceiling_usd"] == "250"


def test_mutation_disabled_when_no_token_configured(monkeypatch):
    monkeypatch.delenv("CONFIG_API_TOKEN", raising=False)
    client = TestClient(build_app(FakeDapr()))
    resp = client.put("/config/thresholds", json={"ceiling_usd": "500"}, headers=AUTH)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "patch",
    [
        {"ceiling_usd": "-1"},          # negative money
        {"ceiling_usd": "999999"},      # above the hard cap
        {"min_confidence": 1.5},        # confidence out of [0, 1]
        {"min_confidence": -0.1},
        {"saas_monthly_cap_usd": "0"},  # non-positive category cap
        {"totally_unknown_knob": 1},    # unknown keys are rejected
        {"fx_rates": {"USD": "0"}},     # zero FX rate would convert totals to $0
        {"fx_rates": {"EUR": "-1.08"}},  # negative FX rate
    ],
    ids=["negative-ceiling", "ceiling-over-cap", "confidence-gt-1",
         "confidence-negative", "zero-category-cap", "unknown-key",
         "zero-fx-rate", "negative-fx-rate"],
)
def test_invalid_values_are_rejected(client, patch):
    resp = client.put("/config/thresholds", json=patch, headers=AUTH)
    assert resp.status_code == 422
    # The posture is unchanged after the rejected attempt.
    assert client.get("/config/thresholds").json()["ceiling_usd"] == "250"


def test_policy_endpoint(client):
    assert "summary" in client.get("/config/policy").json()
