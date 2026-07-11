"""Approval service: escalation queue + one-action decision (F4/F5/M10)."""

import asyncio

import httpx
import pytest
from approvalflow_approval.app import build_app
from approvalflow_approval.store import InMemoryQueueStore, QueueItem, QueueItemStatus
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


class FailOnceGateway(FakeGateway):
    """Fails the FIRST raise_human_decision, then behaves normally — models a
    side effect that fails after the queue item has been claimed RESOLVED."""

    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    async def raise_human_decision(self, tracking_id, decision) -> None:
        if not self._failed:
            self._failed = True
            raise RuntimeError("decision service unavailable")
        await super().raise_human_decision(tracking_id, decision)


def test_side_effects_replayed_after_gateway_failure():
    """High: a claim whose side effects fail must NOT be lost. The retry replays
    the workflow resume + resolved publish exactly once (no early return that
    silently drops them)."""
    gateway = FailOnceGateway()
    # raise_server_exceptions=False so the failed side effect surfaces as a 500
    # the client would retry, rather than propagating into the test.
    client = TestClient(
        build_app(store=InMemoryQueueStore(), gateway=gateway),
        raise_server_exceptions=False,
    )
    _enqueue(client)

    first = client.post("/api/approvals/t-1/decision", json={"action": "approve"})
    assert first.status_code == 500  # side effect failed → surfaced for retry
    assert gateway.raised == []  # nothing resumed yet
    assert gateway.resolved_events == []

    second = client.post("/api/approvals/t-1/decision", json={"action": "approve"})
    assert second.status_code == 200
    assert second.json()["status"] == QueueItemStatus.RESOLVED
    assert second.json()["side_effects_completed"] is True
    # Replayed exactly once — not zero (lost), not twice.
    assert [tid for tid, _ in gateway.raised] == ["t-1"]
    assert len(gateway.resolved_events) == 1


class SlowGateway(FakeGateway):
    """Holds inside raise_human_decision long enough for a racing request to
    reach the side-effects region — proves the lease serialises them."""

    async def raise_human_decision(self, tracking_id, decision) -> None:
        await asyncio.sleep(0.05)
        await super().raise_human_decision(tracking_id, decision)


@pytest.mark.anyio
async def test_concurrent_posts_raise_workflow_once():
    """High: two approvers POST at the same time and the gateway is slow. The
    side-effects lease must let only ONE request resume the workflow / publish."""
    store = InMemoryQueueStore()
    await store.upsert(QueueItem(escalation=EscalationRequested.model_validate(escalation())))
    gateway = SlowGateway()
    app = build_app(store=store, gateway=gateway)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r1, r2 = await asyncio.gather(
            ac.post("/api/approvals/t-1/decision", json={"action": "approve"}),
            ac.post("/api/approvals/t-1/decision", json={"action": "approve"}),
        )

    assert r1.status_code == 200 and r2.status_code == 200
    # Exactly one request ran the side effects, despite racing.
    assert [tid for tid, _ in gateway.raised] == ["t-1"]
    assert len(gateway.resolved_events) == 1
    assert (await store.get("t-1")).side_effects_completed is True


@pytest.mark.anyio
async def test_concurrent_claims_resolve_exactly_once():
    """M10: two approvers act on the same pending item at once — the atomic
    PENDING→RESOLVED claim lets exactly one win; the other gets None."""
    store = InMemoryQueueStore()
    await store.upsert(QueueItem(escalation=EscalationRequested.model_validate(escalation("t-x"))))
    a, b = await asyncio.gather(
        store.try_resolve("t-x", HumanDecision(action="approve", approver="a@x")),
        store.try_resolve("t-x", HumanDecision(action="reject", approver="b@x")),
    )
    assert [a is not None, b is not None].count(True) == 1  # one winner
    resolved = await store.get("t-x")
    assert resolved.status == QueueItemStatus.RESOLVED
