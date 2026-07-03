"""Intake HTTP surface via TestClient (in-memory backends, no Dapr needed)."""

import pytest
from approvalflow_intake.app import build_app
from approvalflow_intake.publisher import InMemoryPublisher
from approvalflow_intake.service import IntakeService
from approvalflow_intake.store import InMemorySubmissionStore
from approvalflow_shared.contracts import ItemStatus, Topics
from approvalflow_shared.correlation import CORRELATION_HEADER
from fastapi.testclient import TestClient


@pytest.fixture
def publisher() -> InMemoryPublisher:
    return InMemoryPublisher()


@pytest.fixture
def client(publisher) -> TestClient:
    service = IntakeService(InMemorySubmissionStore(), publisher)
    return TestClient(build_app(service))


def test_submit_returns_202_with_tracking_id(client, fixtures_by_id):
    resp = client.post("/api/invoices", json=fixtures_by_id["INV-1001"])
    assert resp.status_code == 202
    body = resp.json()
    assert body["trackingId"]
    # Tracking id equals the correlation id echoed in the response header (F9/M14).
    assert body["trackingId"] == resp.headers[CORRELATION_HEADER]
    assert resp.headers["Location"] == body["statusUrl"]


def test_status_endpoint_returns_plain_language_reason(client, fixtures_by_id):
    tracking_id = client.post("/api/invoices", json=fixtures_by_id["INV-1001"]).json()["trackingId"]
    status_resp = client.get(f"/api/invoices/{tracking_id}")
    assert status_resp.status_code == 200
    record = status_resp.json()
    assert record["status"] == ItemStatus.RECEIVED
    assert "awaiting" in record["reason"].lower()


def test_unknown_tracking_id_is_404(client):
    assert client.get("/api/invoices/does-not-exist").status_code == 404


def test_duplicate_resubmission_is_flagged_and_not_republished(client, publisher, fixtures_by_id):
    client.post("/api/invoices", json=fixtures_by_id["INV-1001"])
    dup = client.post("/api/invoices", json=fixtures_by_id["INV-1007"])
    assert dup.json()["status"] == ItemStatus.DUPLICATE
    # Published exactly once — the duplicate never enters the pipeline.
    assert publisher.topics() == [Topics.ITEM_SUBMITTED]


def test_submitter_supplied_correlation_id_becomes_tracking_id(client, fixtures_by_id):
    resp = client.post(
        "/api/invoices",
        json=fixtures_by_id["INV-1001"],
        headers={CORRELATION_HEADER: "trace-xyz"},
    )
    assert resp.json()["trackingId"] == "trace-xyz"
