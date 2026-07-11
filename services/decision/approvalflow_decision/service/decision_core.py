"""The decision pipeline: policy engine → agent → router → DecisionRecord.

Pure orchestration of the already-tested pieces. No Dapr here so it can be
unit-tested directly; the workflow activities call into this.
"""

from __future__ import annotations

from approvalflow_shared.contracts import InvoiceSubmission
from approvalflow_shared.logging import get_logger

from ..agent import DecisionAgent
from ..agent.assessor import AgentUnavailableError
from ..models import (
    ROUTE_TO_STATUS,
    AgentAssessment,
    AutonomyThresholds,
    DecisionRecord,
    Recommendation,
)
from ..policy_engine import evaluate
from ..router import route

log = get_logger()


def _fail_safe_assessment(invoice: InvoiceSubmission) -> AgentAssessment:
    """The assessment used when the model provider is unavailable.

    Confidence 0.0 is below any `min_confidence`, so the router forces
    human_review — never auto-approve. This does NOT violate M15 ("never
    silently default to an *approval*"): we default to the least-autonomy
    outcome and log loudly, rather than stranding the submitter at `received`
    when the agent is down."""
    return AgentAssessment(
        category=invoice.category,
        risk="unknown",
        confidence=0.0,
        recommendation=Recommendation.ESCALATE,
        reasoning="Agent unavailable — routed to human review.",
    )


async def decide(
    tracking_id: str,
    correlation_id: str,
    invoice: InvoiceSubmission,
    thresholds: AutonomyThresholds,
    agent: DecisionAgent,
) -> DecisionRecord:
    evaluation = evaluate(invoice, thresholds)
    try:
        assessment = await agent.assess(invoice)
    except AgentUnavailableError as exc:
        # Fail SAFE, not stuck: the deterministic policy engine still ran; route
        # the item to a human instead of crashing the workflow (which would
        # leave the submitter at `received` forever).
        log.error("agent_unavailable_fail_safe", tracking_id=tracking_id, error=str(exc))
        assessment = _fail_safe_assessment(invoice)
    decision = route(evaluation, assessment, thresholds)

    record = DecisionRecord(
        tracking_id=tracking_id,
        correlation_id=correlation_id,
        invoice_id=invoice.id,
        route=decision.route,
        status=ROUTE_TO_STATUS[decision.route],
        reason=decision.reason,
        cited_rules=decision.cited_rules,
        usd_amount=str(evaluation.usd_amount),
        assessment=assessment,
    )
    log.info(
        "decision_made",
        tracking_id=tracking_id,
        invoice_id=invoice.id,
        route=decision.route,
        usd_amount=str(evaluation.usd_amount),
    )
    return record
