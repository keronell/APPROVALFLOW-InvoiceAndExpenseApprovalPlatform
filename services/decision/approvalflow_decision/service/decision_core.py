"""The decision pipeline: policy engine → agent → router → DecisionRecord.

Pure orchestration of the already-tested pieces. No Dapr here so it can be
unit-tested directly; the workflow activities call into this.
"""

from __future__ import annotations

from approvalflow_shared.contracts import InvoiceSubmission
from approvalflow_shared.logging import get_logger

from ..agent import DecisionAgent
from ..models import ROUTE_TO_STATUS, AutonomyThresholds, DecisionRecord
from ..policy_engine import evaluate
from ..router import route

log = get_logger()


async def decide(
    tracking_id: str,
    correlation_id: str,
    invoice: InvoiceSubmission,
    thresholds: AutonomyThresholds,
    agent: DecisionAgent,
) -> DecisionRecord:
    evaluation = evaluate(invoice, thresholds)
    assessment = await agent.assess(invoice)
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
