"""Aggregation + audit logic, storage-agnostic (DIP)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from typing import Protocol, runtime_checkable

from approvalflow_shared.logging import get_logger
from pydantic import BaseModel, Field

log = get_logger()

STATE_STORE = "statestore"
METRICS_KEY = "metrics"


class Metrics(BaseModel):
    """What the controller dashboard shows (F8)."""

    submitted: int = 0
    auto_approved: int = 0
    escalated: int = 0
    rejected_by_router: int = 0
    human_approved: int = 0
    human_rejected: int = 0
    sent_back: int = 0
    paid: int = 0
    payment_failed: int = 0
    auto_approved_usd: str = "0"
    human_approved_usd: str = "0"

    def add_usd(self, field: str, amount: str) -> None:
        current = Decimal(getattr(self, field))
        setattr(self, field, str(current + Decimal(amount)))

    @property
    def auto_approval_rate(self) -> float:
        decided = self.auto_approved + self.escalated + self.rejected_by_router
        return round(self.auto_approved / decided, 3) if decided else 0.0


class AuditEntry(BaseModel):
    topic: str
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    payload: dict


MetricsMutation = Callable[[Metrics], None]


@runtime_checkable
class ReportStore(Protocol):
    """At-least-once safety contract: dedupe is recorded atomically WITH each
    write (never as a separate pre-claim), so a crash before the write lets the
    redelivery retry, and a redelivery after the write is a no-op (M10)."""

    async def get_metrics(self) -> Metrics: ...

    async def update_metrics(self, event_key: str, mutate: MetricsMutation) -> None:
        """Apply ``mutate`` exactly once per ``event_key``. The applied-key
        record must be persisted in the same atomic write as the metrics."""
        ...

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        """Idempotent per (tracking_id, entry.topic): appending a topic that is
        already in the trail is a no-op."""
        ...

    async def get_audit(self, tracking_id: str) -> list[AuditEntry]: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._metrics = Metrics()
        self._audit: dict[str, list[AuditEntry]] = {}
        self._applied: set[str] = set()

    async def get_metrics(self) -> Metrics:
        return self._metrics

    async def update_metrics(self, event_key: str, mutate: MetricsMutation) -> None:
        if event_key in self._applied:
            return
        mutate(self._metrics)
        self._applied.add(event_key)

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        trail = self._audit.setdefault(tracking_id, [])
        if any(e.topic == entry.topic for e in trail):
            return
        trail.append(entry)

    async def get_audit(self, tracking_id: str) -> list[AuditEntry]:
        return self._audit.get(tracking_id, [])


class ReportingService:
    """Consumes pipeline events; every event lands in the audit trail and the
    relevant counters. Metric updates are keyed off topics, not payload shape,
    so new event fields never break reporting."""

    def __init__(self, store: ReportStore) -> None:
        self._store = store

    async def record(self, topic: str, payload: dict) -> None:
        tracking_id = payload.get("trackingId") or payload.get("tracking_id") or "unknown"

        # Each business topic legitimately fires at most once per item, so
        # (topic, tracking id) identifies the event. Both writes below are
        # idempotent per event and record their dedupe atomically with the
        # write itself — a crash between them (or before either) simply lets
        # the pub/sub redelivery retry; nothing is ever marked "seen" first
        # and then lost.
        await self._store.append_audit(tracking_id, AuditEntry(topic=topic, payload=payload))
        await self._store.update_metrics(
            f"{topic}::{tracking_id}", lambda m: _apply(m, topic, payload)
        )

    async def summary(self) -> dict:
        metrics = await self._store.get_metrics()
        return {**metrics.model_dump(), "auto_approval_rate": metrics.auto_approval_rate}

    async def audit_trail(self, tracking_id: str) -> list[AuditEntry]:
        return await self._store.get_audit(tracking_id)


def _apply(metrics: Metrics, topic: str, payload: dict) -> None:
    """Pure metric increments for one event (runs inside the store's retry loop)."""
    if topic == "item.submitted":
        metrics.submitted += 1
    elif topic == "decision.made":
        route = payload.get("route", "")
        usd = payload.get("usdAmount", "0")
        if route == "auto_approve":
            metrics.auto_approved += 1
            metrics.add_usd("auto_approved_usd", usd)
        elif route == "human_review":
            metrics.escalated += 1
        elif route == "reject":
            metrics.rejected_by_router += 1
    elif topic == "escalation.resolved":
        action = payload.get("action", "")
        usd = payload.get("usdAmount", "0")
        if action == "approve":
            metrics.human_approved += 1
            metrics.add_usd("human_approved_usd", usd)
        elif action == "reject":
            metrics.human_rejected += 1
        elif action == "send_back":
            metrics.sent_back += 1
    elif topic == "payment.completed":
        metrics.paid += 1
    elif topic == "payment.failed":
        metrics.payment_failed += 1
