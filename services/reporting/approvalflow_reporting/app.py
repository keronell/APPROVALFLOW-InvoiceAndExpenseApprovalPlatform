"""Reporting service HTTP surface + subscriptions."""

from __future__ import annotations

import os

from approvalflow_shared.contracts import Topics
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Request

from .service import (
    METRICS_KEY,
    STATE_STORE,
    AuditEntry,
    InMemoryReportStore,
    Metrics,
    ReportingService,
)

log = get_logger()

_TOPICS = [
    Topics.ITEM_SUBMITTED,
    Topics.DECISION_MADE,
    Topics.ESCALATION_REQUESTED,
    Topics.ESCALATION_RESOLVED,
    Topics.PAYMENT_COMPLETED,
    Topics.PAYMENT_FAILED,
]


class DaprReportStore:
    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    async def get_metrics(self) -> Metrics:
        value = await self._dapr.get_state(STATE_STORE, METRICS_KEY)
        return Metrics.model_validate(value) if value else Metrics()

    async def put_metrics(self, metrics: Metrics) -> None:
        await self._dapr.save_state(STATE_STORE, METRICS_KEY, metrics.model_dump(mode="json"))

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        key = f"audit::{tracking_id}"
        trail: list = await self._dapr.get_state(STATE_STORE, key) or []  # type: ignore[assignment]
        trail.append(entry.model_dump(mode="json"))
        await self._dapr.save_state(STATE_STORE, key, trail)

    async def get_audit(self, tracking_id: str) -> list[AuditEntry]:
        trail: list = await self._dapr.get_state(STATE_STORE, f"audit::{tracking_id}") or []  # type: ignore[assignment]
        return [AuditEntry.model_validate(e) for e in trail]


def build_app(service: ReportingService | None = None):
    app = create_app("reporting")
    if service is None:
        if os.environ.get("STORE_BACKEND", "dapr") == "memory":
            service = ReportingService(InMemoryReportStore())
        else:
            service = ReportingService(DaprReportStore(DaprIO()))

    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {"pubsubname": Topics.PUBSUB_NAME, "topic": topic, "route": f"/events/{topic}"}
            for topic in _TOPICS
        ]

    for topic in _TOPICS:
        # One handler per topic route (closure captures the topic name).
        def _make_handler(topic_name: str):
            async def handler(request: Request) -> dict:
                envelope = await request.json()
                payload = envelope.get("data", envelope)
                await service.record(topic_name, payload)
                return {"status": "SUCCESS"}

            return handler

        app.post(f"/events/{topic}", tags=["dapr"])(_make_handler(topic))

    @app.get("/api/reports/summary", tags=["reporting"])
    async def summary() -> dict:
        """Controller dashboard numbers (F8)."""
        return await service.summary()

    @app.get("/api/audit/{tracking_id}", tags=["reporting"])
    async def audit(tracking_id: str) -> list[AuditEntry]:
        """The complete decision trail for one item, by correlation id (F9)."""
        return await service.audit_trail(tracking_id)

    return app


app = build_app()
