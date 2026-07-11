"""Payment service — consumes payment.requested and runs the saga workflow."""

from __future__ import annotations

from contextlib import asynccontextmanager

import dapr.ext.workflow as wf
from approvalflow_shared.auth import require_role
from approvalflow_shared.contracts import PaymentRequested, Topics
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Depends, Request

from .budget_dapr import STATE_STORE, DaprBudgetStore
from .workflow import payment_workflow, wfr

log = get_logger()


@asynccontextmanager
async def _lifespan(app):
    wfr.start()
    log.info("payment_workflow_runtime_started")
    app.state.wf_client = wf.DaprWorkflowClient()
    app.state.dapr = DaprIO()
    try:
        yield
    finally:
        await app.state.dapr.aclose()
        wfr.shutdown()


def build_app():
    app = create_app("payment", lifespan=_lifespan)

    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.PAYMENT_REQUESTED,
                "route": "/events/payment-requested",
            }
        ]

    @app.post("/events/payment-requested", tags=["dapr"])
    async def on_payment_requested(request: Request) -> dict:
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        req = PaymentRequested.model_validate(payload)
        client: wf.DaprWorkflowClient = app.state.wf_client

        # Idempotency (M10): tracking id is the instance id → no double payment.
        if client.get_workflow_state(req.tracking_id, fetch_payloads=False) is not None:
            log.info("payment_already_started", tracking_id=req.tracking_id)
            return {"status": "SUCCESS"}

        client.schedule_new_workflow(
            payment_workflow,
            input=req.model_dump(mode="json", by_alias=True),
            instance_id=req.tracking_id,
        )
        log.info("payment_workflow_scheduled", tracking_id=req.tracking_id)
        return {"status": "SUCCESS"}

    @app.get(
        "/api/budgets/{department}",
        tags=["payment"],
        dependencies=[Depends(require_role("approver"))],
    )
    async def get_budget(department: str) -> dict:
        store = DaprBudgetStore(app.state.dapr)
        available, _ = await store.get_budget(department)
        return {"department": department, "available": str(available)}

    @app.get(
        "/api/payments/{tracking_id}",
        tags=["payment"],
        dependencies=[Depends(require_role())],  # any authenticated role
    )
    async def get_payment(tracking_id: str) -> dict:
        record = await app.state.dapr.get_state(STATE_STORE, f"payment::{tracking_id}")
        return record or {"status": "pending"}

    return app


app = build_app()
