"""The payment saga as a Dapr Workflow (M9).

Forward steps each have a compensating action:

    ReserveBudget ── ExecutePayment ── ConfirmPayment
         │                 │
    (ReleaseBudget) ◀──────┘  on failure

If ExecutePayment fails, the reservation is released — no orphaned reservation,
no partial or double payment. Every activity is idempotent (M10).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import dapr.ext.workflow as wf
from approvalflow_shared.contracts import (
    ItemStatus,
    PaymentRequested,
    StatusChanged,
    Topics,
)
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger

from .budget import BudgetLedger
from .budget_dapr import STATE_STORE, DaprBudgetStore
from .provider import execute_payment

log = get_logger()
wfr = wf.WorkflowRuntime()


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
@wfr.workflow(name="payment_workflow")
def payment_workflow(ctx: wf.DaprWorkflowContext, req: dict):
    reservation = yield ctx.call_activity(reserve_activity, input=req)
    if not reservation["ok"]:
        yield ctx.call_activity(
            fail_activity, input={"req": req, "reason": reservation["reason"]}
        )
        return {"status": "rejected", "reason": reservation["reason"]}

    try:
        yield ctx.call_activity(pay_activity, input=req)
    except Exception:  # ExecutePayment failed → compensate.
        yield ctx.call_activity(release_activity, input=req)
        yield ctx.call_activity(fail_activity, input={"req": req, "reason": "payment declined"})
        return {"status": "failed"}

    yield ctx.call_activity(confirm_activity, input=req)
    yield ctx.call_activity(complete_activity, input=req)
    return {"status": "paid"}


# --------------------------------------------------------------------------- #
# Activities
# --------------------------------------------------------------------------- #
def _ledger(dapr: DaprIO) -> BudgetLedger:
    return BudgetLedger(DaprBudgetStore(dapr))


@wfr.activity(name="reserve_activity")
def reserve_activity(ctx: wf.WorkflowActivityContext, req: dict) -> dict:
    return asyncio.run(_reserve(req))


async def _reserve(req: dict) -> dict:
    request = PaymentRequested.model_validate(req)
    dapr = DaprIO()
    try:
        result = await _ledger(dapr).reserve(
            request.tracking_id, request.invoice.department, Decimal(request.usd_amount)
        )
    finally:
        await dapr.aclose()
    log.info("budget_reserve", tracking_id=request.tracking_id, ok=result.ok, reason=result.reason)
    return result.model_dump()


@wfr.activity(name="pay_activity")
def pay_activity(ctx: wf.WorkflowActivityContext, req: dict) -> str:
    return asyncio.run(_pay(req))


async def _pay(req: dict) -> str:
    request = PaymentRequested.model_validate(req)
    key = f"payment::{request.tracking_id}"
    # The provider idempotency key: retried calls with the same key yield the
    # same payment at the provider, never a second charge.
    idempotency_key = request.tracking_id
    dapr = DaprIO()
    try:
        # Idempotency (M10): if already paid, don't charge again.
        existing = await dapr.get_state(STATE_STORE, key)
        if existing and existing.get("status") == "paid":  # type: ignore[union-attr]
            return str(existing.get("paymentId"))  # type: ignore[union-attr]

        # Record a durable pending attempt BEFORE the external side effect: if
        # the provider succeeds but we crash before persisting the result, the
        # retry sees the attempt and re-calls the provider with the SAME
        # idempotency key — the provider deduplicates, so exactly one charge.
        await dapr.save_state(
            STATE_STORE,
            key,
            {
                "status": "attempting",
                "idempotencyKey": idempotency_key,
                "amount": request.usd_amount,
            },
        )
        payment_id = execute_payment(request.invoice, request.usd_amount, idempotency_key)
        await dapr.save_state(
            STATE_STORE,
            key,
            {
                "status": "paid",
                "paymentId": payment_id,
                "idempotencyKey": idempotency_key,
                "amount": request.usd_amount,
            },
        )
        return payment_id
    finally:
        await dapr.aclose()


@wfr.activity(name="release_activity")
def release_activity(ctx: wf.WorkflowActivityContext, req: dict) -> None:
    asyncio.run(_release(req))


async def _release(req: dict) -> None:
    request = PaymentRequested.model_validate(req)
    dapr = DaprIO()
    try:
        await _ledger(dapr).release(request.tracking_id)
    finally:
        await dapr.aclose()
    log.info("budget_released", tracking_id=request.tracking_id)


@wfr.activity(name="confirm_activity")
def confirm_activity(ctx: wf.WorkflowActivityContext, req: dict) -> None:
    asyncio.run(_confirm(req))


async def _confirm(req: dict) -> None:
    request = PaymentRequested.model_validate(req)
    dapr = DaprIO()
    try:
        await _ledger(dapr).confirm(request.tracking_id)
    finally:
        await dapr.aclose()


@wfr.activity(name="complete_activity")
def complete_activity(ctx: wf.WorkflowActivityContext, req: dict) -> None:
    asyncio.run(
        _publish_status(req, ItemStatus.PAID, Topics.PAYMENT_COMPLETED, "Payment completed.")
    )


@wfr.activity(name="fail_activity")
def fail_activity(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    req = payload["req"]
    reason = payload.get("reason", "payment failed")
    asyncio.run(
        _mark_failed(req, reason)
    )


async def _mark_failed(req: dict, reason: str) -> None:
    request = PaymentRequested.model_validate(req)
    dapr = DaprIO()
    try:
        await dapr.save_state(
            STATE_STORE,
            f"payment::{request.tracking_id}",
            {"status": "failed", "reason": reason, "amount": request.usd_amount},
        )
    finally:
        await dapr.aclose()
    await _publish_status(
        req, ItemStatus.PAYMENT_FAILED, Topics.PAYMENT_FAILED, f"Payment failed: {reason}."
    )


async def _publish_status(req: dict, status: ItemStatus, topic: str, reason: str) -> None:
    request = PaymentRequested.model_validate(req)
    dapr = DaprIO()
    try:
        await dapr.publish(
            topic,
            StatusChanged(
                tracking_id=request.tracking_id,
                correlation_id=request.correlation_id,
                status=status,
                reason=reason,
            ),
            correlation_id=request.correlation_id,
        )
        await dapr.publish(
            Topics.ITEM_STATUS_CHANGED,
            StatusChanged(
                tracking_id=request.tracking_id,
                correlation_id=request.correlation_id,
                status=status,
                reason=reason,
            ),
            correlation_id=request.correlation_id,
        )
    finally:
        await dapr.aclose()
    log.info("payment_status_published", tracking_id=request.tracking_id, status=str(status))
