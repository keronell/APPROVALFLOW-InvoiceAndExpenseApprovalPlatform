"""Escalation-queue storage abstraction (DIP): in-memory for tests, Dapr for prod."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Protocol, runtime_checkable

from approvalflow_shared.contracts import ApprovalAction, EscalationRequested
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


@runtime_checkable
class QueueStore(Protocol):
    async def upsert(self, item: QueueItem) -> None: ...

    async def get(self, tracking_id: str) -> QueueItem | None: ...

    async def list_items(self) -> list[QueueItem]: ...


class InMemoryQueueStore:
    def __init__(self) -> None:
        self._items: dict[str, QueueItem] = {}

    async def upsert(self, item: QueueItem) -> None:
        self._items[item.escalation.tracking_id] = item

    async def get(self, tracking_id: str) -> QueueItem | None:
        return self._items.get(tracking_id)

    async def list_items(self) -> list[QueueItem]:
        return sorted(self._items.values(), key=lambda i: i.escalated_at)


class DaprQueueStore:
    """Items keyed per tracking id + a small index key for listing."""

    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

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
