"""Inbox logic, storage-agnostic (DIP): in-memory for tests, Dapr for prod."""

from __future__ import annotations

import datetime as dt
from typing import Protocol, runtime_checkable

from approvalflow_shared.contracts import ItemStatus, ItemSubmitted, StatusChanged
from approvalflow_shared.logging import get_logger
from pydantic import BaseModel, Field

log = get_logger()

STATE_STORE = "statestore"

# Outcomes worth notifying about. Intermediate states (received, processing,
# pending_approval, approved, paying, auto_approved) are visible on the status
# endpoint but do not generate inbox noise. `duplicate` never reaches here:
# intake short-circuits duplicates without publishing.
TERMINAL_STATUSES = {
    ItemStatus.PAID,
    ItemStatus.PAYMENT_FAILED,
    ItemStatus.REJECTED,
    ItemStatus.SENT_BACK,
}


class Notification(BaseModel):
    tracking_id: str
    invoice_id: str
    submitter: str
    status: ItemStatus
    reason: str
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))


class ItemRef(BaseModel):
    """What we remember from item.submitted to address the notification later."""

    submitter: str
    invoice_id: str


@runtime_checkable
class InboxStore(Protocol):
    async def put_ref(self, tracking_id: str, ref: ItemRef) -> None: ...

    async def get_ref(self, tracking_id: str) -> ItemRef | None: ...

    async def append(self, submitter: str, notification: Notification) -> None:
        """Idempotent per (tracking_id, status): the dedupe must commit in the
        same atomic write as the inbox entry — never as a separate pre-claim,
        so a failed append can be retried by the redelivery and a redelivery
        after a successful append is a no-op (M10)."""
        ...

    async def inbox(self, submitter: str) -> list[Notification]: ...


class InMemoryInboxStore:
    def __init__(self) -> None:
        self._refs: dict[str, ItemRef] = {}
        self._inboxes: dict[str, list[Notification]] = {}

    async def put_ref(self, tracking_id: str, ref: ItemRef) -> None:
        self._refs[tracking_id] = ref

    async def get_ref(self, tracking_id: str) -> ItemRef | None:
        return self._refs.get(tracking_id)

    async def append(self, submitter: str, notification: Notification) -> None:
        inbox = self._inboxes.setdefault(submitter, [])
        if any(
            n.tracking_id == notification.tracking_id and n.status == notification.status
            for n in inbox
        ):
            return
        inbox.append(notification)

    async def inbox(self, submitter: str) -> list[Notification]:
        return sorted(
            self._inboxes.get(submitter, []), key=lambda n: n.at, reverse=True
        )


class NotificationService:
    def __init__(self, store: InboxStore) -> None:
        self._store = store

    async def on_item_submitted(self, event: ItemSubmitted) -> None:
        await self._store.put_ref(
            event.tracking_id,
            ItemRef(submitter=event.invoice.submitter, invoice_id=event.invoice.id),
        )

    async def on_status_changed(self, event: StatusChanged) -> None:
        if event.status not in TERMINAL_STATUSES:
            return
        ref = await self._store.get_ref(event.tracking_id)
        if ref is None:
            # status event for an item we never saw submitted — nothing to address.
            log.warning("notification_for_unknown_item", tracking_id=event.tracking_id)
            return
        # Idempotent on redelivery: append() dedupes per (item, terminal
        # status) atomically with the inbox write itself — a failed append is
        # retried by the redelivery; a successful one makes it a no-op.
        await self._store.append(
            ref.submitter,
            Notification(
                tracking_id=event.tracking_id,
                invoice_id=ref.invoice_id,
                submitter=ref.submitter,
                status=event.status,
                reason=event.reason,
            ),
        )
        log.info(
            "notification_delivered",
            tracking_id=event.tracking_id,
            submitter=ref.submitter,
            status=str(event.status),
        )

    async def inbox(self, submitter: str) -> list[Notification]:
        return await self._store.inbox(submitter)
