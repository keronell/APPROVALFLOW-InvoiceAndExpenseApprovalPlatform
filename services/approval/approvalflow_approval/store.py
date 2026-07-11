"""Escalation-queue storage abstraction (DIP): in-memory for tests, Dapr for prod."""

from __future__ import annotations

import asyncio
import datetime as dt
from enum import StrEnum
from typing import Protocol, runtime_checkable

from approvalflow_shared.contracts import ApprovalAction, EscalationRequested, HumanDecision
from approvalflow_shared.daprio import DaprIO
from pydantic import BaseModel, Field

STATE_STORE = "statestore"
INDEX_KEY = "queue-index"


class QueueItemStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class QueueItem(BaseModel):
    """One escalated item as the approver sees it (F4)."""

    escalation: EscalationRequested
    status: QueueItemStatus = QueueItemStatus.PENDING
    action: ApprovalAction | None = None
    approver: str = ""
    note: str = ""
    escalated_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    resolved_at: dt.datetime | None = None


def _resolve(item: QueueItem, decision: HumanDecision) -> None:
    """Stamp the human decision onto a pending item (in place)."""
    item.status = QueueItemStatus.RESOLVED
    item.action = decision.action
    item.approver = decision.approver
    item.note = decision.note
    item.resolved_at = dt.datetime.now(dt.UTC)


@runtime_checkable
class QueueStore(Protocol):
    async def upsert(self, item: QueueItem) -> None: ...

    async def get(self, tracking_id: str) -> QueueItem | None: ...

    async def list_items(self) -> list[QueueItem]: ...

    async def try_resolve(
        self, tracking_id: str, decision: HumanDecision
    ) -> QueueItem | None:
        """Atomically transition PENDING→RESOLVED, stamping ``decision``.

        Returns the resolved item if this caller won the claim, or ``None`` if
        the item is missing or already resolved. This is what stops two
        concurrent approvers from both raising the workflow event and publishing
        conflicting ``escalation.resolved`` events (M10)."""
        ...


class InMemoryQueueStore:
    def __init__(self) -> None:
        self._items: dict[str, QueueItem] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, item: QueueItem) -> None:
        self._items[item.escalation.tracking_id] = item

    async def get(self, tracking_id: str) -> QueueItem | None:
        return self._items.get(tracking_id)

    async def list_items(self) -> list[QueueItem]:
        return sorted(self._items.values(), key=lambda i: i.escalated_at)

    async def try_resolve(
        self, tracking_id: str, decision: HumanDecision
    ) -> QueueItem | None:
        async with self._lock:
            item = self._items.get(tracking_id)
            if item is None or item.status == QueueItemStatus.RESOLVED:
                return None
            _resolve(item, decision)
            self._items[tracking_id] = item
            return item


class DaprQueueStore:
    """Items keyed per tracking id + a small index key for listing."""

    _RETRIES = 8

    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    async def try_resolve(
        self, tracking_id: str, decision: HumanDecision
    ) -> QueueItem | None:
        key = f"queue::{tracking_id}"
        for _ in range(self._RETRIES):
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            if value is None:
                return None
            item = QueueItem.model_validate(value)
            if item.status == QueueItemStatus.RESOLVED:
                return None  # already resolved by someone else
            _resolve(item, decision)
            # First-write CAS: only the caller whose ETag still matches wins the
            # PENDING→RESOLVED transition; the loser re-reads and sees RESOLVED.
            if await self._dapr.try_save_state(
                STATE_STORE, key, item.model_dump(mode="json"), etag=etag
            ):
                return item
        return None

    async def upsert(self, item: QueueItem) -> None:
        tid = item.escalation.tracking_id
        await self._dapr.save_state(STATE_STORE, f"queue::{tid}", item.model_dump(mode="json"))
        index: list = await self._dapr.get_state(STATE_STORE, INDEX_KEY) or []  # type: ignore[assignment]
        if tid not in index:
            index.append(tid)
            await self._dapr.save_state(STATE_STORE, INDEX_KEY, index)

    async def get(self, tracking_id: str) -> QueueItem | None:
        value = await self._dapr.get_state(STATE_STORE, f"queue::{tracking_id}")
        return QueueItem.model_validate(value) if value else None

    async def list_items(self) -> list[QueueItem]:
        index: list = await self._dapr.get_state(STATE_STORE, INDEX_KEY) or []  # type: ignore[assignment]
        items = []
        for tid in index:
            item = await self.get(tid)
            if item is not None:
                items.append(item)
        return sorted(items, key=lambda i: i.escalated_at)
