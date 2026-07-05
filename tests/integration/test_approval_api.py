"""Approval service: escalation queue + one-action decision (F4/F5/M10)."""

import pytest
from approvalflow_approval.app import build_app
from approvalflow_approval.store import InMemoryQueueStore, QueueItemStatus
from approvalflow_shared.contracts import EscalationRequested, HumanDecision
from fastapi.testclient import TestClient


class FakeGateway:
    """Records raised events instead of invoking the decision service."""

    def __init__(self) -> None:
        self.raised: list[tuple[str, HumanDecision]] = []
        self.resolved_events = []

    async def raise_human_decision(self, tracking_id, decision) -> None:
        self.raised.append((tracking_id, decision))

    async def publish_resolved(self, event) -> None:
        self.resolved_events.append(event)


def escalation(tid: str = "t-1") -> dict:
    return EscalationRequested(
        tracking_id=tid,
        correlation_id=tid,
        invoice_id="INV-1003",
        submitter="lena.schmidt@northwind.example",
        department="sales-2026Q2",
        vendor="The Rooftop Grill",
        usd_amount="1820.00",
        reason="Client entertainment over $500 missing client name; over ceiling.",
        cited_rules=["MEAL-02", "AUTONOMY-CEILING"],
        recommendation="escalate",
        confidence=0.9,
        risk="medium",
    ).model_dump(mode="json", by_alias=True)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(gateway) -> TestClient:
    return TestClient(build_app(store=InMemoryQueueStore(), gateway=gateway))


def _enqueue(client, tid="t-1"):
    resp = client.post("/events/escalation-requested", json={"data": escalation(tid)})
    assert resp.status_code == 200


def test_queue_shows_agent_rationale(client):
    """F4: the queue item carries recommendation, confidence, and cited rules."""
    _enqueue(client)
    items = client.get("/api/approvals").json()
    assert len(items) == 1
    esc = items[0]["escalation"]
    assert esc["recommendation"] == "escalate"
    assert esc["confidence"] == 0.9
    assert "MEAL-02" in esc["citedRules"]


def test_one_action_resolves_and_raises_event(client, gateway):
    """F5: a single POST both resumes the workflow and resolves the queue item."""
    _enqueue(client)
    resp = client.post("/api/approvals/t-1/decision", json={"action": "approve"})
    assert resp.status_code == 200
    assert resp.json()["status"] == QueueItemStatus.RESOLVED
    assert [tid for tid, _ in gateway.raised] == ["t-1"]
    assert gateway.resolved_events[0].action == "approve"
    # Resolved items leave the pending queue (F6: no rubber-stamp pile-up).
    assert client.get("/api/approvals").json() == []


def test_second_action_is_idempotent(client, gateway):
    """M10: deciding twice has exactly one effect."""
    _enqueue(client)
    client.post("/api/approvals/t-1/decision", json={"action": "approve"})
    again = client.post("/api/approvals/t-1/decision", json={"action": "reject"})
    assert again.status_code == 200
    assert again.json()["action"] == "approve"  # first decision stands
    assert len(gateway.raised) == 1


def test_redelivered_escalation_does_not_reopen_resolved_item(client):
    _enqueue(client)
    client.post("/api/approvals/t-1/decision", json={"action": "reject"})
    _enqueue(client)  # pub/sub redelivery of the same escalation
    items = client.get("/api/approvals?status=all").json()
    assert len(items) == 1
    assert items[0]["status"] == QueueItemStatus.RESOLVED


def test_send_back_action(client, gateway):
    _enqueue(client, "t-2")
    resp = client.post(
        "/api/approvals/t-2/decision",
        json={"action": "send_back", "note": "attach the client name"},
    )
    assert resp.json()["action"] == "send_back"
    assert gateway.raised[0][1].note == "attach the client name"


def test_unknown_item_is_404(client):
    resp = client.post("/api/approvals/nope/decision", json={"action": "approve"})
    assert resp.status_code == 404
