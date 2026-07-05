"""Aggregation + audit logic, storage-agnostic (DIP)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

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


@runtime_checkable
class ReportStore(Protocol):
    async def get_metrics(self) -> Metrics: ...

    async def put_metrics(self, metrics: Metrics) -> None: ...

    async def append_audit(self, tracking_id: str, entry: AuditEntry) -> None: ...

    async def get_audit(self, tracking_id: str) -> list[AuditEntry]: ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._metrics = Metrics()
        self._audit: dict[str, list[AuditEntry]] = {}

    async def get_metrics(self) -> Metrics:
        return self._metrics

    async def put_metrics(self, metrics: Metrics) -> None:
        self._metrics = metrics

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
        await self._store.append_audit(tracking_id, AuditEntry(topic=topic, payload=payload))

        metrics = await self._store.get_metrics()
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
        await self._store.put_metrics(metrics)

    async def summary(self) -> dict:
        metrics = await self._store.get_metrics()
        return {**metrics.model_dump(), "auto_approval_rate": metrics.auto_approval_rate}

    async def audit_trail(self, tracking_id: str) -> list[AuditEntry]:
        return await self._store.get_audit(tracking_id)
