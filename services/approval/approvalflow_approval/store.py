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

# How long a side-effect lease is honoured before another request may take it
# over. Only matters for crash recovery: a request that acquires the lease and
# dies without releasing it strands the item until the lease goes stale. Normal
# failures release the lease immediately, so a retry does not wait this out.
SIDE_EFFECT_LEASE_TTL = dt.timedelta(seconds=30)


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
    # Outbox flag: the claim (PENDING→RESOLVED) and the resulting side effects
    # (resume the workflow, publish escalation.resolved) are NOT one transaction.
    # This stays False until both side effects have completed, so a claim whose
    # side effects failed is replayed by a retry instead of being lost.
    side_effects_completed: bool = False
    # Lease held by the single request currently executing the side effects, so
    # concurrent retries don't run them in parallel. Cleared on success (flag
    # set) or on failure (released for immediate retry); honoured for
    # SIDE_EFFECT_LEASE_TTL otherwise, for crash recovery.
    side_effects_lease_at: dt.datetime | None = None


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

    async def try_begin_side_effects(self, tracking_id: str) -> QueueItem | None:
        """Atomically acquire the side-effects lease for a RESOLVED item.

        Returns the item if this caller may run the side effects (they aren't
        done and no fresh lease is held), else ``None``. This is the atomic
        "I am the request executing the side effects" claim that keeps two
        concurrent retries from both resuming the workflow / publishing."""
        ...

    async def release_side_effects_lease(self, tracking_id: str) -> None:
        """Drop the lease without marking completion, so a retry can re-acquire
        immediately after a failed side effect."""
        ...

    async def mark_side_effects_completed(self, tracking_id: str) -> QueueItem | None:
        """Flip ``side_effects_completed`` to True once the workflow resume and
        escalation.resolved publish have both succeeded. Returns the updated
        item (or None if it vanished). Idempotent."""
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

    async def try_begin_side_effects(self, tracking_id: str) -> QueueItem | None:
        async with self._lock:
            item = self._items.get(tracking_id)
            if item is None or item.side_effects_completed:
                return None
            now = dt.datetime.now(dt.UTC)
            lease = item.side_effects_lease_at
            if lease is not None and now - lease < SIDE_EFFECT_LEASE_TTL:
                return None  # another request holds a fresh lease
            item.side_effects_lease_at = now
            self._items[tracking_id] = item
            return item

    async def release_side_effects_lease(self, tracking_id: str) -> None:
        async with self._lock:
            item = self._items.get(tracking_id)
            if item is not None and not item.side_effects_completed:
                item.side_effects_lease_at = None
                self._items[tracking_id] = item

    async def mark_side_effects_completed(self, tracking_id: str) -> QueueItem | None:
        async with self._lock:
            item = self._items.get(tracking_id)
            if item is None:
                return None
            item.side_effects_completed = True
            item.side_effects_lease_at = None
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

    async def try_begin_side_effects(self, tracking_id: str) -> QueueItem | None:
        key = f"queue::{tracking_id}"
        for _ in range(self._RETRIES):
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            if value is None:
                return None
            item = QueueItem.model_validate(value)
            if item.side_effects_completed:
                return None
            now = dt.datetime.now(dt.UTC)
            lease = item.side_effects_lease_at
            if lease is not None and now - lease < SIDE_EFFECT_LEASE_TTL:
                return None  # another request holds a fresh lease
            item.side_effects_lease_at = now
            # First-write CAS: exactly one racing request wins the lease.
            if await self._dapr.try_save_state(
                STATE_STORE, key, item.model_dump(mode="json"), etag=etag
            ):
                return item
        return None

    async def release_side_effects_lease(self, tracking_id: str) -> None:
        key = f"queue::{tracking_id}"
        for _ in range(self._RETRIES):
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            if value is None:
                return
            item = QueueItem.model_validate(value)
            if item.side_effects_completed or item.side_effects_lease_at is None:
                return
            item.side_effects_lease_at = None
            if await self._dapr.try_save_state(
                STATE_STORE, key, item.model_dump(mode="json"), etag=etag
            ):
                return

    async def mark_side_effects_completed(self, tracking_id: str) -> QueueItem | None:
        key = f"queue::{tracking_id}"
        for _ in range(self._RETRIES):
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            if value is None:
                return None
            item = QueueItem.model_validate(value)
            if item.side_effects_completed:
                return item  # already marked (idempotent)
            item.side_effects_completed = True
            item.side_effects_lease_at = None
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
