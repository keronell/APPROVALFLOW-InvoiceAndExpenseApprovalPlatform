"""Approval service HTTP surface + escalation subscription."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from approvalflow_shared.contracts import (
    EscalationRequested,
    EscalationResolved,
    HumanDecision,
    Topics,
)
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import HTTPException, Request

from .store import InMemoryQueueStore, QueueItem, QueueItemStatus, QueueStore

log = get_logger()

DECISION_APP_ID = "decision"


@runtime_checkable
class DecisionGateway(Protocol):
    """How the approver's action reaches the paused workflow (DIP for tests)."""

    async def raise_human_decision(self, tracking_id: str, decision: HumanDecision) -> None: ...

    async def publish_resolved(self, event: EscalationResolved) -> None: ...


class DaprDecisionGateway:
    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    async def raise_human_decision(self, tracking_id: str, decision: HumanDecision) -> None:
        await self._dapr.invoke(
            DECISION_APP_ID,
            f"api/decisions/{tracking_id}/human-decision",
            verb="POST",
            data=decision.model_dump(mode="json", by_alias=True),
            correlation_id=tracking_id,
        )

    async def publish_resolved(self, event: EscalationResolved) -> None:
        await self._dapr.publish(
            Topics.ESCALATION_RESOLVED, event, correlation_id=event.correlation_id
        )


def build_app(store: QueueStore | None = None, gateway: DecisionGateway | None = None):
    app = create_app("approval")

    if store is None or gateway is None:
        if os.environ.get("STORE_BACKEND", "dapr") == "memory":
            store = store or InMemoryQueueStore()
        else:
            from .store import DaprQueueStore

            dapr = DaprIO()
            store = store or DaprQueueStore(dapr)
            gateway = gateway or DaprDecisionGateway(dapr)
        if gateway is None:  # memory mode without an explicit gateway (tests)
            raise RuntimeError("memory mode requires an explicit DecisionGateway")

    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.ESCALATION_REQUESTED,
                "route": "/events/escalation-requested",
            }
        ]

    @app.post("/events/escalation-requested", tags=["dapr"])
    async def on_escalation(request: Request) -> dict:
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        escalation = EscalationRequested.model_validate(payload)
        # Idempotent on redelivery: never clobber an already-resolved item (M10).
        existing = await store.get(escalation.tracking_id)
        if existing is None:
            await store.upsert(QueueItem(escalation=escalation))
            log.info("escalation_queued", tracking_id=escalation.tracking_id)
        return {"status": "SUCCESS"}

    @app.get("/api/approvals", tags=["approval"])
    async def list_queue(status: str = "pending") -> list[QueueItem]:
        """The approver's queue — only escalated items, with the agent's
        recommendation, confidence, and cited rules (F4)."""
        items = await store.list_items()
        if status == "all":
            return items
        return [i for i in items if i.status == status]

    @app.post("/api/approvals/{tracking_id}/decision", tags=["approval"])
    async def decide(tracking_id: str, decision: HumanDecision) -> QueueItem:
        """Approve / reject / send back in ONE action (F5): raises the event on
        the paused workflow and resolves the queue item."""
        item = await store.get(tracking_id)
        if item is None:
            raise HTTPException(status_code=404, detail="No such escalated item")
        if item.status == QueueItemStatus.RESOLVED:
            return item  # idempotent: acting twice has one effect (M10)

        # Claim the PENDING→RESOLVED transition ATOMICALLY *before* raising the
        # workflow event. Two concurrent approvers race here; only the winner
        # gets an item back and proceeds to raise + publish, so we never emit
        # conflicting escalation.resolved events (M10).
        claimed = await store.try_resolve(tracking_id, decision)
        if claimed is None:
            # Lost the race (or already resolved) — return the resolved item,
            # doing no new work.
            return await store.get(tracking_id) or item

        await gateway.raise_human_decision(tracking_id, decision)
        await gateway.publish_resolved(
            EscalationResolved(
                tracking_id=tracking_id,
                correlation_id=claimed.escalation.correlation_id,
                action=decision.action,
                usd_amount=claimed.escalation.usd_amount,
                approver=decision.approver,
            )
        )
        log.info("escalation_resolved", tracking_id=tracking_id, action=str(decision.action))
        return claimed

    return app


app = build_app()
