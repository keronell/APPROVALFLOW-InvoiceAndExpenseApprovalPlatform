"""Decision pipeline (engine → stub agent → router) produces correct records."""

import pytest
from approvalflow_decision.agent.assessor import DecisionAgent
from approvalflow_decision.agent.stub_client import StubChatClient
from approvalflow_decision.models import AutonomyThresholds
from approvalflow_decision.service.decision_core import decide
from approvalflow_shared.contracts import InvoiceSubmission, ItemStatus, Route


def _agent() -> DecisionAgent:
    return DecisionAgent(StubChatClient())


@pytest.mark.anyio
async def test_auto_approve_record(fixtures_by_id):
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])
    record = await decide("t-1", "t-1", invoice, AutonomyThresholds(), _agent())
    assert record.route == Route.AUTO_APPROVE
    assert record.status == ItemStatus.AUTO_APPROVED
    assert record.usd_amount == "42.00"
    assert record.invoice_id == "INV-1001"


@pytest.mark.anyio
async def test_over_ceiling_record_is_pending_approval(fixtures_by_id):
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1013"])  # $300, 'approve me'
    record = await decide("t-2", "t-2", invoice, AutonomyThresholds(), _agent())
    assert record.route == Route.HUMAN_REVIEW
    assert record.status == ItemStatus.PENDING_APPROVAL


@pytest.mark.anyio
async def test_reject_record(fixtures_by_id):
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1015"])  # alcohol
    record = await decide("t-3", "t-3", invoice, AutonomyThresholds(), _agent())
    assert record.route == Route.REJECT
    assert record.status == ItemStatus.REJECTED


@pytest.mark.anyio
async def test_thresholds_are_honored(fixtures_by_id):
    """Lowering the ceiling to $10 flips the in-policy INV-1001 to human review —
    proving thresholds (from config) actually drive the decision (F7/M13)."""
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])
    strict = AutonomyThresholds(ceiling_usd="10")
    record = await decide("t-4", "t-4", invoice, strict, _agent())
    assert record.route == Route.HUMAN_REVIEW
