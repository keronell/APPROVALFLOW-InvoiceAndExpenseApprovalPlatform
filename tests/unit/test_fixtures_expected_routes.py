"""The regression floor: every shipped fixture must land on its labeled route.

The assessments used here simulate an *over-eager* agent that confidently
recommends "approve" for nearly everything. That is deliberate: it demonstrates
M12/F10 at the level of the whole labeled set — only the genuinely safe items
auto-approve; the router escalates or rejects the rest regardless of what the
agent wanted.
"""

from approvalflow_decision import AgentAssessment, Recommendation, evaluate, route
from approvalflow_shared.contracts import InvoiceSubmission, Route

# INV-1007 is a re-submission of INV-1001; it only routes `duplicate` against
# already-processed state (that state lives in intake, not the pure engine).
ALREADY_PROCESSED = {"INV-1007"}

# INV-1010 is the ambiguous "team offsite" bundle — a correct agent reports low
# confidence. Every other fixture gets a confident "approve" to prove the router
# holds the line on its own.
LOW_CONFIDENCE = {"INV-1010"}


def _assessment(fixture: dict) -> AgentAssessment:
    confidence = 0.60 if fixture["id"] in LOW_CONFIDENCE else 0.95
    return AgentAssessment(
        category=fixture["category"],
        confidence=confidence,
        recommendation=Recommendation.APPROVE,
        reasoning="simulated over-eager agent",
    )


def _route_for(fixture: dict) -> Route:
    invoice = InvoiceSubmission.model_validate(fixture)
    evaluation = evaluate(invoice)
    decision = route(
        evaluation,
        _assessment(fixture),
        already_processed=fixture["id"] in ALREADY_PROCESSED,
    )
    return decision.route


def test_all_fixtures_present(fixtures_doc: dict) -> None:
    assert len(fixtures_doc["fixtures"]) == 20


def test_every_fixture_matches_expected_route(fixtures_doc: dict) -> None:
    failures = []
    for fixture in fixtures_doc["fixtures"]:
        expected = Route(fixture["expected"]["route"])
        actual = _route_for(fixture)
        if actual != expected:
            failures.append(f"{fixture['id']}: expected {expected}, got {actual}")
    assert not failures, "Route mismatches:\n" + "\n".join(failures)


def test_at_least_two_no_human_auto_approvals(fixtures_doc: dict) -> None:
    """Anti-cheese: the posture must actually auto-approve real items (no $0 dodge)."""
    auto = [
        f["id"]
        for f in fixtures_doc["fixtures"]
        if _route_for(f) == Route.AUTO_APPROVE
    ]
    assert len(auto) >= 2, f"Expected >=2 auto-approvals, got {auto}"


def test_prompt_injection_note_does_not_flip_decision(fixtures_by_id: dict) -> None:
    """INV-1013's note says 'Approve me…'. Even with the agent confidently
    recommending approve, the router escalates on the deterministic amount."""
    fixture = fixtures_by_id["INV-1013"]
    assert "approve me" in fixture["notes"].lower()
    assert _route_for(fixture) == Route.HUMAN_REVIEW
