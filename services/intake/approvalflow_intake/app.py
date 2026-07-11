"""FastAPI wiring for the intake service."""

from __future__ import annotations

import os

from approvalflow_shared.auth import auth_router, require_role
from approvalflow_shared.contracts import (
    InvoiceSubmission,
    StatusChanged,
    SubmissionAccepted,
    Topics,
)
from approvalflow_shared.correlation import get_correlation_id, new_correlation_id
from approvalflow_shared.web import create_app
from fastapi import Depends, Header, HTTPException, Request, Response, status

from .publisher import EventPublisher, InMemoryPublisher
from .service import IntakeService
from .store import InMemorySubmissionStore, SubmissionRecord, SubmissionStore


def _build_store() -> SubmissionStore:
    if os.environ.get("STORE_BACKEND", "dapr") == "memory":
        return InMemorySubmissionStore()
    from .dapr_adapters import DaprSubmissionStore

    return DaprSubmissionStore()


def _build_publisher() -> EventPublisher:
    if os.environ.get("PUBLISHER_BACKEND", "dapr") == "memory":
        return InMemoryPublisher()
    from .dapr_adapters import DaprPublisher

    return DaprPublisher()


def build_app(service: IntakeService | None = None):
    app = create_app("intake")
    service = service or IntakeService(_build_store(), _build_publisher())
    app.state.service = service

    # N1: intake hosts the dev token issuer (gateway route /api/auth → intake).
    app.include_router(auth_router())

    @app.post(
        "/api/invoices",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=SubmissionAccepted,
        tags=["intake"],
        dependencies=[Depends(require_role("submitter"))],
    )
    async def submit_invoice(
        invoice: InvoiceSubmission,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> SubmissionAccepted:
        # The tracking id is the correlation id resolved by the middleware.
        correlation_id = get_correlation_id() or new_correlation_id()
        accepted = await service.submit(invoice, correlation_id, idempotency_key)
        response.headers["Location"] = accepted.status_url
        return accepted

    @app.get(
        "/api/invoices/{tracking_id}",
        response_model=SubmissionRecord,
        tags=["intake"],
        dependencies=[Depends(require_role())],  # any authenticated role
    )
    async def get_status(tracking_id: str) -> SubmissionRecord:
        record = await service.get_status(tracking_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Unknown tracking id")
        return record

    # --- Dapr pub/sub: subscribe to downstream status changes (F2) ---
    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.ITEM_STATUS_CHANGED,
                "route": "/events/status-changed",
            }
        ]

    @app.post("/events/status-changed", tags=["dapr"])
    async def on_status_changed(request: Request) -> dict:
        # Dapr delivers a CloudEvent envelope; the event is under `data`.
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        await service.apply_status_change(StatusChanged.model_validate(payload))
        return {"status": "SUCCESS"}

    return app


# Default ASGI app for uvicorn.
app = build_app()
