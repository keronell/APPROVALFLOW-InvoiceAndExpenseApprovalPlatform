"""Notification service: per-submitter inbox from pipeline events (M8)."""

import pytest
from approvalflow_notification.app import build_app
from approvalflow_notification.service import InMemoryInboxStore, NotificationService
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_app(NotificationService(InMemoryInboxStore())))


def _submit(client, fixtures_by_id, fixture_id="INV-1001", tid="t-1"):
    resp = client.post(
        "/events/item-submitted",
        json={"data": {"trackingId": tid, "correlationId": tid,
                       "invoice": fixtures_by_id[fixture_id]}},
    )
    assert resp.status_code == 200


def _status(client, tid, status, reason="done"):
    resp = client.post(
        "/events/status-changed",
        json={"data": {"trackingId": tid, "correlationId": tid,
                       "status": status, "reason": reason}},
    )
    assert resp.status_code == 200


def test_terminal_status_lands_in_submitter_inbox(client, fixtures_by_id):
    _submit(client, fixtures_by_id)
    _status(client, "t-1", "paid", "Payment completed.")

    inbox = client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json()
    assert len(inbox) == 1
    assert inbox[0]["invoice_id"] == "INV-1001"
    assert inbox[0]["status"] == "paid"
    assert "payment completed" in inbox[0]["reason"].lower()


def test_intermediate_statuses_do_not_notify(client, fixtures_by_id):
    _submit(client, fixtures_by_id)
    for status in ("received", "processing", "pending_approval", "approved", "auto_approved"):
        _status(client, "t-1", status)
    inbox = client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json()
    assert inbox == []


def test_redelivered_terminal_event_notifies_once(client, fixtures_by_id):
    """M10: one notification per (item, terminal status), even on redelivery."""
    _submit(client, fixtures_by_id)
    _status(client, "t-1", "paid")
    _status(client, "t-1", "paid")  # pub/sub redelivery
    inbox = client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json()
    assert len(inbox) == 1


def test_unknown_item_is_dropped(client):
    _status(client, "never-seen", "paid")
    # No crash, and no inbox got the orphan event.
    assert client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json() == []


def test_empty_inbox_for_unknown_submitter(client):
    assert client.get(
        "/api/notifications", params={"submitter": "nobody@northwind.example"}
    ).json() == []
