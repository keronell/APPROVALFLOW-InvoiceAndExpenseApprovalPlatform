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
    async def claim_event(self, event_key: str) -> bool:
        """Atomically claim an event key; False if it was already processed.
        This is the at-least-once dedupe: a redelivered event must not inflate
        counters or duplicate audit entries (M10)."""
        ...

    async def get_metrics(self) -> Metrics: ...

    async def update_metrics(self, mutate: MetricsMutation) -> None:
        """Apply ``mutate`` to the current metrics atomically. Implementations
        must re-read and re-apply on write conflicts so concurrent events never
        lose each other's updates."""
        ...

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None: ...

    async def get_audit(self, tracking_id: str) -> list[AuditEntry]: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._metrics = Metrics()
        self._audit: dict[str, list[AuditEntry]] = {}
        self._seen: set[str] = set()

    async def claim_event(self, event_key: str) -> bool:
        if event_key in self._seen:
            return False
        self._seen.add(event_key)
        return True

    async def get_metrics(self) -> Metrics:
        return self._metrics

    async def update_metrics(self, mutate: MetricsMutation) -> None:
        mutate(self._metrics)

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None:
        self._audit.setdefault(tracking_id, []).append(entry)

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
        # (topic, tracking id) identifies the event. A pub/sub redelivery
        # loses this claim and is dropped before touching audit or metrics.
        if not await self._store.claim_event(f"{topic}::{tracking_id}"):
            log.info("event_redelivery_ignored", topic=topic, tracking_id=tracking_id)
            return

        await self._store.append_audit(tracking_id, AuditEntry(topic=topic, payload=payload))
        # The mutation is applied inside the store's conflict-retry loop, so a
        # concurrent event's increments are never overwritten.
        await self._store.update_metrics(lambda m: _apply(m, topic, payload))

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
