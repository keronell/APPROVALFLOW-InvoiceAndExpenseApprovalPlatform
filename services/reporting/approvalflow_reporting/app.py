"""Reporting service HTTP surface + subscriptions."""

from __future__ import annotations

import os

from approvalflow_shared.auth import require_role
from approvalflow_shared.contracts import Topics
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Depends, Request
from pydantic import BaseModel, Field

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


class MetricsState(BaseModel):
    """Metrics + the applied event keys, persisted as ONE document so the
    dedupe record commits in the same atomic write as the aggregate — a crash
    can never mark an event applied without its effect (and vice versa)."""

    metrics: Metrics = Field(default_factory=Metrics)
    applied: list[str] = Field(default_factory=list)


# Keep the applied-key window bounded; a redelivery arriving after 1000
# subsequent events is beyond RabbitMQ's practical redelivery horizon here.
_APPLIED_CAP = 1000


class DaprReportStore:
    """Writes are idempotent per event and ETag-conditional with retry, so
    concurrent events neither double count nor overwrite each other's updates,
    and a redelivery after a crash retries the whole write (no pre-claim)."""

    _RETRIES = 8

    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    @staticmethod
    def _load_state(value: object | None) -> MetricsState:
        """Parse the stored document, migrating the pre-MetricsState flat
        `Metrics` shape so an upgrade never resets existing counters."""
        if value is None:
            return MetricsState()
        if isinstance(value, dict) and "metrics" in value:
            return MetricsState.model_validate(value)
        # Legacy flat Metrics document (no `metrics` wrapper): preserve it.
        return MetricsState(metrics=Metrics.model_validate(value), applied=[])

    async def get_metrics(self) -> Metrics:
        value = await self._dapr.get_state(STATE_STORE, METRICS_KEY)
        return self._load_state(value).metrics

    async def _conditional_save(self, key: str, value: object, etag: str | None) -> bool:
        """First write of a key uses atomic insert; later writes are
        ETag-conditional — no lost updates in either phase."""
        if etag is None:
            return await self._dapr.try_insert_state(STATE_STORE, key, value)
        return await self._dapr.try_save_state(STATE_STORE, key, value, etag=etag)

    async def update_metrics(self, event_key: str, mutate) -> None:
        # Full read-modify-write inside the retry loop; the applied-key check
        # rides in the same document, so "already counted" and the count
        # itself commit together.
        for _ in range(self._RETRIES):
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, METRICS_KEY)
            state = self._load_state(value)
            if event_key in state.applied:
                return
            mutate(state.metrics)
            state.applied = (state.applied + [event_key])[-_APPLIED_CAP:]
            if await self._conditional_save(
                METRICS_KEY, state.model_dump(mode="json"), etag
            ):
                return
        raise RuntimeError("could not persist metrics after retries")

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        key = f"audit::{tracking_id}"
        for _ in range(self._RETRIES):
            trail, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            trail = list(trail or [])  # type: ignore[arg-type]
            # Idempotent per (tracking id, topic): the dedupe check runs against
            # the same snapshot the conditional write commits.
            if any(e.get("topic") == entry.topic for e in trail):
                return
            trail.append(entry.model_dump(mode="json"))
            if await self._conditional_save(key, trail, etag):
                return
        raise RuntimeError(f"could not append audit entry for {tracking_id}")

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

    @app.get(
        "/api/reports/summary",
        tags=["reporting"],
        dependencies=[Depends(require_role("approver"))],
    )
    async def summary() -> dict:
        """Controller dashboard numbers (F8)."""
        return await service.summary()

    @app.get(
        "/api/audit/{tracking_id}",
        tags=["reporting"],
        dependencies=[Depends(require_role("admin"))],
    )
    async def audit(tracking_id: str) -> list[AuditEntry]:
        """The complete decision trail for one item, by correlation id (F9)."""
        return await service.audit_trail(tracking_id)

    return app


app = build_app()
