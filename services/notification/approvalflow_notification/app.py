"""Notification service HTTP surface + subscriptions."""

from __future__ import annotations

import os

from approvalflow_shared.auth import require_role
from approvalflow_shared.contracts import ItemSubmitted, StatusChanged, Topics
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Depends, Request

from .service import (
    STATE_STORE,
    InMemoryInboxStore,
    ItemRef,
    Notification,
    NotificationService,
    RefNotReadyError,
)

log = get_logger()


class DaprInboxStore:
    _RETRIES = 8

    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    async def put_ref(self, tracking_id: str, ref: ItemRef) -> None:
        await self._dapr.save_state(
            STATE_STORE, f"ref::{tracking_id}", ref.model_dump(mode="json")
        )

    async def get_ref(self, tracking_id: str) -> ItemRef | None:
        value = await self._dapr.get_state(STATE_STORE, f"ref::{tracking_id}")
        return ItemRef.model_validate(value) if value else None

    async def append(self, submitter: str, notification: Notification) -> None:
        # Conditional append (insert on first write, ETag afterwards) with the
        # (tracking id, status) dedupe checked against the SAME snapshot the
        # write commits — no separate pre-claim, so a failed append is simply
        # retried by the pub/sub redelivery and never lost.
        key = f"inbox::{submitter}"
        for _ in range(self._RETRIES):
            inbox, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
            inbox = list(inbox or [])  # type: ignore[arg-type]
            if any(
                n.get("tracking_id") == notification.tracking_id
                and n.get("status") == notification.status
                for n in inbox
            ):
                return
            inbox.append(notification.model_dump(mode="json"))
            if etag is None:
                if await self._dapr.try_insert_state(STATE_STORE, key, inbox):
                    return
            elif await self._dapr.try_save_state(STATE_STORE, key, inbox, etag=etag):
                return
        raise RuntimeError(f"could not append notification for {submitter}")

    async def inbox(self, submitter: str) -> list[Notification]:
        inbox: list = await self._dapr.get_state(STATE_STORE, f"inbox::{submitter}") or []  # type: ignore[assignment]
        notifications = [Notification.model_validate(n) for n in inbox]
        return sorted(notifications, key=lambda n: n.at, reverse=True)


def build_app(service: NotificationService | None = None):
    app = create_app("notification")
    if service is None:
        if os.environ.get("STORE_BACKEND", "dapr") == "memory":
            service = NotificationService(InMemoryInboxStore())
        else:
            service = NotificationService(DaprInboxStore(DaprIO()))

    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.ITEM_SUBMITTED,
                "route": "/events/item-submitted",
            },
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.ITEM_STATUS_CHANGED,
                "route": "/events/status-changed",
            },
        ]

    @app.post("/events/item-submitted", tags=["dapr"])
    async def on_item_submitted(request: Request) -> dict:
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        await service.on_item_submitted(ItemSubmitted.model_validate(payload))
        return {"status": "SUCCESS"}

    @app.post("/events/status-changed", tags=["dapr"])
    async def on_status_changed(request: Request) -> dict:
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        try:
            await service.on_status_changed(StatusChanged.model_validate(payload))
        except RefNotReadyError:
            # Terminal status arrived before item.submitted — tell Dapr to
            # redeliver rather than dropping the notification.
            return {"status": "RETRY"}
        return {"status": "SUCCESS"}

    @app.get(
        "/api/notifications",
        tags=["notification"],
        dependencies=[Depends(require_role())],  # any authenticated role
    )
    async def get_inbox(submitter: str) -> list[Notification]:
        """The submitter's inbox — final outcomes, newest first (M8)."""
        return await service.inbox(submitter)

    return app


app = build_app()
