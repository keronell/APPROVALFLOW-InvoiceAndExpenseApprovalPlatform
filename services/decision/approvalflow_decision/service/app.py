"""Decision service — subscribes to item.submitted and runs the Dapr Workflow."""

from __future__ import annotations

from contextlib import asynccontextmanager

import dapr.ext.workflow as wf
from approvalflow_shared.contracts import ItemSubmitted, Topics
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Request

from .workflow import STATE_STORE, decision_workflow, wfr

log = get_logger()


@asynccontextmanager
async def _lifespan(app):
    # Start the workflow worker (connects to the sidecar over gRPC).
    wfr.start()
    log.info("workflow_runtime_started")
    app.state.wf_client = wf.DaprWorkflowClient()
    app.state.dapr = DaprIO()
    try:
        yield
    finally:
        await app.state.dapr.aclose()
        wfr.shutdown()
        log.info("workflow_runtime_stopped")


def build_app():
    app = create_app("decision", lifespan=_lifespan)

    @app.get("/dapr/subscribe", tags=["dapr"])
    async def dapr_subscribe() -> list[dict]:
        return [
            {
                "pubsubname": Topics.PUBSUB_NAME,
                "topic": Topics.ITEM_SUBMITTED,
                "route": "/events/item-submitted",
            }
        ]

    @app.post("/events/item-submitted", tags=["dapr"])
    async def on_item_submitted(request: Request) -> dict:
        envelope = await request.json()
        payload = envelope.get("data", envelope)
        submitted = ItemSubmitted.model_validate(payload)
        client: wf.DaprWorkflowClient = app.state.wf_client

        # Idempotency (M10): the tracking id is the workflow instance id, so a
        # redelivered event does not start a second decision.
        existing = client.get_workflow_state(submitted.tracking_id, fetch_payloads=False)
        if existing is not None:
            log.info("workflow_already_started", tracking_id=submitted.tracking_id)
            return {"status": "SUCCESS"}

        client.schedule_new_workflow(
            decision_workflow,
            input=submitted.model_dump(mode="json", by_alias=True),
            instance_id=submitted.tracking_id,
        )
        log.info("workflow_scheduled", tracking_id=submitted.tracking_id)
        return {"status": "SUCCESS"}

    @app.get("/api/decisions/{tracking_id}", tags=["decision"])
    async def get_decision(tracking_id: str) -> dict:
        record = await app.state.dapr.get_state(STATE_STORE, f"decision::{tracking_id}")
        return record or {"status": "pending"}

    return app


app = build_app()
