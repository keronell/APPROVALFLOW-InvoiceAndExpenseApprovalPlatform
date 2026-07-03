"""The stub chat client + DecisionAgent run the real MAF Agent loop offline.

These tests prove the agent path works end-to-end (structured output parsed
into a typed AgentAssessment) without any network, and that the whole
agent→router pipeline reproduces the fixture routes with the stub model.
"""

import pytest
from approvalflow_decision import evaluate, route
from approvalflow_decision.agent.assessor import AgentUnavailableError, DecisionAgent
from approvalflow_decision.agent.settings import LLMSettings
from approvalflow_decision.agent.stub_client import StubChatClient, assess_invoice
from approvalflow_decision.models import AgentAssessment, Recommendation
from approvalflow_shared.contracts import InvoiceSubmission, Route


def test_stub_assessment_high_confidence_for_clear_categories(fixtures_by_id):
    a = assess_invoice(fixtures_by_id["INV-1001"])
    assert isinstance(a, AgentAssessment)
    assert a.confidence >= 0.80
    assert a.recommendation == Recommendation.APPROVE


def test_stub_assessment_low_confidence_for_ambiguous_other(fixtures_by_id):
    a = assess_invoice(fixtures_by_id["INV-1010"])  # bundled "other" offsite
    assert a.confidence < 0.80


def test_stub_flags_alcohol_as_reject(fixtures_by_id):
    a = assess_invoice(fixtures_by_id["INV-1015"])
    assert a.recommendation == Recommendation.REJECT
    assert "MEAL-03" in a.policy_violations


def test_stub_ignores_steering_notes(fixtures_by_id):
    """INV-1013's notes say 'Approve me…'. The stub must not be steered: it
    decides from structured fields only, so it does not fabricate approval."""
    fixture = fixtures_by_id["INV-1013"]
    assert "approve me" in fixture["notes"].lower()
    a = assess_invoice(fixture)
    # SaaS from a known vendor → the stub does recommend approve, but that is
    # based on category/vendor, not the note; the router still escalates on amount.
    assert a.reasoning.startswith("[stub]")


@pytest.mark.anyio
async def test_decision_agent_runs_maf_loop_with_stub(fixtures_by_id):
    agent = DecisionAgent(StubChatClient())
    invoice = InvoiceSubmission.model_validate(fixtures_by_id["INV-1001"])
    assessment = await agent.assess(invoice)
    assert isinstance(assessment, AgentAssessment)
    assert assessment.confidence >= 0.80


@pytest.mark.anyio
async def test_agent_then_router_reproduces_fixture_routes(fixtures_doc):
    """End-to-end with the stub model: agent recommends, router decides."""
    agent = DecisionAgent(StubChatClient())
    already = {"INV-1007"}
    failures = []
    for fixture in fixtures_doc["fixtures"]:
        invoice = InvoiceSubmission.model_validate(fixture)
        assessment = await agent.assess(invoice)
        decision = route(evaluate(invoice), assessment, already_processed=fixture["id"] in already)
        expected = Route(fixture["expected"]["route"])
        if decision.route != expected:
            failures.append(f"{fixture['id']}: expected {expected}, got {decision.route}")
    assert not failures, "Route mismatches:\n" + "\n".join(failures)


def test_stub_provider_selected_from_settings():
    from approvalflow_decision.agent.client import build_chat_client

    client = build_chat_client(LLMSettings(provider="stub"))
    assert isinstance(client, StubChatClient)


def test_unknown_provider_fails_fast():
    from approvalflow_decision.agent.client import build_chat_client

    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        build_chat_client(LLMSettings(provider="bogus"))


@pytest.mark.anyio
async def test_agent_raises_on_bad_structured_output():
    """A client that returns nothing parseable must surface an error, never a
    silent default assessment (M15)."""
    from agent_framework import ChatResponse, Content, Message

    class EmptyClient(StubChatClient):
        async def _inner_get_response(self, *, messages, stream, options, **kwargs):
            return ChatResponse(
                messages=[Message(role="assistant", contents=[Content.from_text("not json")])]
            )

    agent = DecisionAgent(EmptyClient())
    invoice = InvoiceSubmission(
        id="INV-X",
        submitter="x@northwind.example",
        department="engineering-2026Q2",
        vendor="Bistro 19",
        vendor_known=True,
        invoice_number="X-1",
        currency="USD",
        category="meals",
        attendees=1,
        line_items=[],
        tax_amount=0,
        total=10,
        receipt_present=True,
        date="2026-05-12",
    )
    with pytest.raises(AgentUnavailableError):
        await agent.assess(invoice)
