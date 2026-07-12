"""Decision pipeline (engine → stub agent → router) produces correct records."""

import pytest
from approvalflow_decision.agent.assessor import AgentUnavailableError, DecisionAgent
from approvalflow_decision.agent.stub_client import StubChatClient
from approvalflow_decision.models import AgentAssessment, AutonomyThresholds
from approvalflow_decision.service.decision_core import decide
from approvalflow_shared.contracts import InvoiceSubmission, ItemStatus, Route
from pydantic import ValidationError


def _agent() -> DecisionAgent:
    return DecisionAgent(StubChatClient())


class _DownAgent:
    """An agent whose provider is unavailable."""

    async def assess(self, invoice):
        raise AgentUnavailableError("provider down")


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
async def test_agent_outage_fails_safe_to_human_review(fixtures_by_id):
    """When the model provider is down, the item is routed to a human — never
    stranded, never auto-approved — even if it would otherwise auto-approve."""
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])  # normally auto
    record = await decide("t-down", "t-down", invoice, AutonomyThresholds(), _DownAgent())
    assert record.route == Route.HUMAN_REVIEW
    assert record.status == ItemStatus.PENDING_APPROVAL
    assert record.assessment.confidence == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(90, 0.90), (85.5, 0.855), (100, 1.0), (0.9, 0.9), (1, 1.0), (0, 0.0)],
)
def test_percent_scale_confidence_is_normalized(raw, expected):
    """Small models sometimes answer the 0.0-1.0 confidence contract in percent
    (e.g. 90); the assessment remaps (1, 100] to a fraction instead of failing
    the whole structured output into the fail-safe path."""
    assessment = AgentAssessment(category="meals", confidence=raw)
    assert assessment.confidence == pytest.approx(expected)


def test_out_of_range_confidence_still_fails_loud():
    for bad in (250, 101, -5):
        with pytest.raises(ValidationError):
            AgentAssessment(category="meals", confidence=bad)


@pytest.mark.anyio
async def test_thresholds_are_honored(fixtures_by_id):
    """Lowering the ceiling to $10 flips the in-policy INV-1001 to human review —
    proving thresholds (from config) actually drive the decision (F7/M13)."""
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])
    strict = AutonomyThresholds(ceiling_usd="10")
    record = await decide("t-4", "t-4", invoice, strict, _agent())
    assert record.route == Route.HUMAN_REVIEW
