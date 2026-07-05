"""Dapr Workflow that orchestrates the decision for one item.

Why a workflow (M9/M11): the same orchestration gains a durable human-in-the-loop
pause (Stage 8, via ``wait_for_external_event``) and drives the payment saga
(Stage 6) without re-plumbing. For an auto-approved item the flow is linear.

Determinism rule: the orchestrator only sequences ``call_activity`` steps — all
I/O (config fetch, agent call, publishing, state writes) lives in activities.
"""

from __future__ import annotations

import asyncio

import dapr.ext.workflow as wf
from approvalflow_shared.contracts import (
    ApprovalAction,
    DecisionMade,
    EscalationRequested,
    HumanDecision,
    ItemStatus,
    ItemSubmitted,
    PaymentRequested,
    StatusChanged,
    Topics,
)
from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger

from ..agent import DecisionAgent, build_chat_client
from ..models import DecisionRecord, Route
from .config_client import fetch_thresholds
from .decision_core import decide

log = get_logger()

STATE_STORE = "statestore"
HUMAN_DECISION_EVENT = "HumanDecision"
wfr = wf.WorkflowRuntime()


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
@wfr.workflow(name="decision_workflow")
def decision_workflow(ctx: wf.DaprWorkflowContext, item: dict):
    """item = ItemSubmitted (dict). Returns the final DecisionRecord (dict).

    For a `human_review` route the workflow pauses durably on an external
    event (M11): its state lives in the Dapr actor state store, so it survives
    a service restart between pause and resume, and the approver's decision
    (raised by the approval service, F5) resumes it exactly where it paused.
    """
    decision = yield ctx.call_activity(decide_activity, input=item)
    yield ctx.call_activity(
        publish_outcome_activity, input={"item": item, "decision": decision}
    )

    if decision["route"] == Route.HUMAN_REVIEW:
        human = yield ctx.wait_for_external_event(HUMAN_DECISION_EVENT)
        decision = yield ctx.call_activity(
            apply_human_decision_activity,
            input={"item": item, "decision": decision, "human": human},
        )

    return decision


# --------------------------------------------------------------------------- #
# Activities (all real work happens here)
# --------------------------------------------------------------------------- #
@wfr.activity(name="decide_activity")
def decide_activity(ctx: wf.WorkflowActivityContext, item: dict) -> dict:
    return asyncio.run(_decide(item))


async def _decide(item: dict) -> dict:
    submitted = ItemSubmitted.model_validate(item)
    dapr = DaprIO()
    try:
        thresholds = await fetch_thresholds(dapr, correlation_id=submitted.correlation_id)
    finally:
        await dapr.aclose()
    agent = DecisionAgent(build_chat_client())
    record = await decide(
        submitted.tracking_id,
        submitted.correlation_id,
        submitted.invoice,
        thresholds,
        agent,
    )
    return record.model_dump(mode="json")


@wfr.activity(name="publish_outcome_activity")
def publish_outcome_activity(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    asyncio.run(_publish_outcome(payload))


async def _publish_outcome(payload: dict) -> None:
    submitted = ItemSubmitted.model_validate(payload["item"])
    record = DecisionRecord.model_validate(payload["decision"])
    correlation_id = record.correlation_id
    dapr = DaprIO()
    try:
        # Persist the full decision for the audit trail (F9).
        await dapr.save_state(
            STATE_STORE, f"decision::{record.tracking_id}", record.model_dump(mode="json")
        )
        # Announce the decision and the submitter-visible status change.
        await dapr.publish(
            Topics.DECISION_MADE,
            DecisionMade(
                tracking_id=record.tracking_id,
                correlation_id=correlation_id,
                invoice_id=record.invoice_id,
                route=str(record.route),
                status=record.status,
                reason=record.reason,
                cited_rules=record.cited_rules,
                usd_amount=record.usd_amount,
            ),
            correlation_id=correlation_id,
        )
        await dapr.publish(
            Topics.ITEM_STATUS_CHANGED,
            StatusChanged(
                tracking_id=record.tracking_id,
                correlation_id=correlation_id,
                status=record.status,
                reason=record.reason,
            ),
            correlation_id=correlation_id,
        )
        # Auto-approved items go straight to payment (consumed from Stage 6 on).
        if record.route == Route.AUTO_APPROVE:
            await dapr.publish(
                Topics.PAYMENT_REQUESTED,
                PaymentRequested(
                    tracking_id=record.tracking_id,
                    correlation_id=correlation_id,
                    invoice=submitted.invoice,
                    usd_amount=record.usd_amount,
                ),
                correlation_id=correlation_id,
            )
        # Escalations carry the agent's rationale to the approver queue (F4).
        elif record.route == Route.HUMAN_REVIEW:
            await dapr.publish(
                Topics.ESCALATION_REQUESTED,
                EscalationRequested(
                    tracking_id=record.tracking_id,
                    correlation_id=correlation_id,
                    invoice_id=record.invoice_id,
                    submitter=submitted.invoice.submitter,
                    department=submitted.invoice.department,
                    vendor=submitted.invoice.vendor,
                    usd_amount=record.usd_amount,
                    reason=record.reason,
                    cited_rules=record.cited_rules,
                    recommendation=str(record.assessment.recommendation),
                    confidence=record.assessment.confidence,
                    risk=record.assessment.risk,
                ),
                correlation_id=correlation_id,
            )
    finally:
        await dapr.aclose()
    log.info("decision_published", tracking_id=record.tracking_id, route=str(record.route))


@wfr.activity(name="apply_human_decision_activity")
def apply_human_decision_activity(ctx: wf.WorkflowActivityContext, payload: dict) -> dict:
    return asyncio.run(_apply_human_decision(payload))


_ACTION_OUTCOME: dict[ApprovalAction, tuple[ItemStatus, str]] = {
    ApprovalAction.APPROVE: (ItemStatus.APPROVED, "Approved by a human reviewer."),
    ApprovalAction.REJECT: (ItemStatus.REJECTED, "Rejected by a human reviewer."),
    ApprovalAction.SEND_BACK: (
        ItemStatus.SENT_BACK,
        "Sent back by a human reviewer — please resubmit with the requested information.",
    ),
}


async def _apply_human_decision(payload: dict) -> dict:
    """Resume point after the durable pause: apply approve/reject/send-back."""
    submitted = ItemSubmitted.model_validate(payload["item"])
    record = DecisionRecord.model_validate(payload["decision"])
    human = HumanDecision.model_validate(payload["human"])
    correlation_id = record.correlation_id

    status, reason = _ACTION_OUTCOME[human.action]
    if human.note:
        reason = f"{reason} Note: {human.note}"
    record.status = status
    record.reason = reason

    dapr = DaprIO()
    try:
        await dapr.save_state(
            STATE_STORE, f"decision::{record.tracking_id}", record.model_dump(mode="json")
        )
        await dapr.publish(
            Topics.ITEM_STATUS_CHANGED,
            StatusChanged(
                tracking_id=record.tracking_id,
                correlation_id=correlation_id,
                status=status,
                reason=reason,
            ),
            correlation_id=correlation_id,
        )
        if human.action == ApprovalAction.APPROVE:
            await dapr.publish(
                Topics.PAYMENT_REQUESTED,
                PaymentRequested(
                    tracking_id=record.tracking_id,
                    correlation_id=correlation_id,
                    invoice=submitted.invoice,
                    usd_amount=record.usd_amount,
                ),
                correlation_id=correlation_id,
            )
    finally:
        await dapr.aclose()
    log.info(
        "human_decision_applied",
        tracking_id=record.tracking_id,
        action=str(human.action),
        approver=human.approver,
    )
    return record.model_dump(mode="json")
