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


def test_terminal_before_submitted_asks_for_retry(client, fixtures_by_id):
    """Out-of-order delivery: a terminal status that arrives before its
    item.submitted must be RETRIED (not dropped), then land after the ref
    exists."""
    resp = client.post(
        "/events/status-changed",
        json={"data": {"trackingId": "t-ooo", "correlationId": "t-ooo",
                       "status": "paid", "reason": "done"}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "RETRY"  # Dapr will redeliver
    # Nothing delivered yet.
    assert client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json() == []

    # item.submitted now arrives; the redelivery lands the notification.
    _submit(client, fixtures_by_id, tid="t-ooo")
    _status(client, "t-ooo", "paid")
    inbox = client.get(
        "/api/notifications", params={"submitter": "dana.cohen@northwind.example"}
    ).json()
    assert len(inbox) == 1
    assert inbox[0]["status"] == "paid"


def test_empty_inbox_for_unknown_submitter(client):
    assert client.get(
        "/api/notifications", params={"submitter": "nobody@northwind.example"}
    ).json() == []


class FlakyInboxStore(InMemoryInboxStore):
    """Fails the first inbox append — models a crash mid-delivery."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_next_append = True

    async def append(self, submitter, notification) -> None:
        if self.fail_next_append:
            self.fail_next_append = False
            raise RuntimeError("transient store failure")
        await super().append(submitter, notification)


@pytest.mark.anyio
async def test_failed_append_is_retried_by_redelivery(fixtures_by_id):
    """The dedupe must not be recorded before the write: if the append fails,
    the handler errors (Dapr redelivers) and the redelivery MUST deliver the
    notification — not find a stale 'seen' marker and drop it."""
    from approvalflow_shared.contracts import InvoiceSubmission, ItemStatus, ItemSubmitted
    from approvalflow_shared.contracts import StatusChanged as SC

    store = FlakyInboxStore()
    service = NotificationService(store)
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])
    await service.on_item_submitted(
        ItemSubmitted(tracking_id="t-1", correlation_id="t-1", invoice=invoice)
    )
    event = SC(tracking_id="t-1", correlation_id="t-1", status=ItemStatus.PAID, reason="done")

    with pytest.raises(RuntimeError):  # first delivery crashes mid-append
        await service.on_status_changed(event)
    await service.on_status_changed(event)  # pub/sub redelivery

    inbox = await service.inbox(invoice.submitter)
    assert len(inbox) == 1  # delivered exactly once, not zero, not two
