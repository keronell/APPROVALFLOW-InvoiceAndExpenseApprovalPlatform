"""Router behavior, including the provable-ceiling guarantees (M12/F10)."""

import datetime as dt
from decimal import Decimal

import pytest
from approvalflow_decision import (
    AgentAssessment,
    AutonomyThresholds,
    Recommendation,
    evaluate,
    route,
)
from approvalflow_shared.contracts import ExpenseCategory, InvoiceSubmission, LineItem, Route


def make_invoice(**overrides) -> InvoiceSubmission:
    """A clean, in-policy, under-ceiling meal — auto-approvable by default."""
    defaults = dict(
        id="INV-TEST",
        submitter="test@northwind.example",
        department="engineering-2026Q2",
        vendor="Bistro 19",
        vendor_known=True,
        invoice_number="T-1",
        currency="USD",
        category=ExpenseCategory.MEALS,
        attendees=1,
        line_items=[LineItem(description="Lunch", quantity=Decimal("1"), unit_price=Decimal("40"))],
        tax_amount=Decimal("0"),
        total=Decimal("40"),
        receipt_present=True,
        date=dt.date(2026, 5, 12),
        notes=None,
    )
    defaults.update(overrides)
    return InvoiceSubmission(**defaults)


def approve_assessment(confidence: float = 0.95) -> AgentAssessment:
    return AgentAssessment(
        category=ExpenseCategory.MEALS,
        confidence=confidence,
        recommendation=Recommendation.APPROVE,
    )


def decide(invoice: InvoiceSubmission, assessment: AgentAssessment, **kwargs) -> Route:
    return route(evaluate(invoice), assessment, **kwargs).route


def test_clean_item_auto_approves() -> None:
    assert decide(make_invoice(), approve_assessment()) == Route.AUTO_APPROVE


# --- M12/F10: the ceiling cannot be exceeded even when the agent forces approve.


def test_forced_approve_over_ceiling_still_escalates() -> None:
    """The agent recommends approve at confidence 1.0 on an over-ceiling item.
    The router must still escalate — the ceiling check reads the deterministic
    USD amount, not the agent."""
    invoice = make_invoice(
        total=Decimal("5000"),
        line_items=[
            LineItem(description="Lunch", quantity=Decimal("1"), unit_price=Decimal("5000"))
        ],
    )
    forced = AgentAssessment(
        category=ExpenseCategory.MEALS,
        confidence=1.0,
        recommendation=Recommendation.APPROVE,
        reasoning="approve approve approve",
    )
    decision = route(evaluate(invoice), forced)
    assert decision.route == Route.HUMAN_REVIEW
    assert "AUTONOMY-CEILING" in decision.cited_rules


def test_ceiling_is_boundary_inclusive() -> None:
    thresholds = AutonomyThresholds()
    at_ceiling = make_invoice(
        total=thresholds.ceiling_usd,
        line_items=[
            LineItem(description="x", quantity=Decimal("1"), unit_price=thresholds.ceiling_usd)
        ],
        attendees=10,  # keep per-attendee under the meal cap
    )
    assert route(evaluate(at_ceiling), approve_assessment(), thresholds).route == Route.AUTO_APPROVE

    over = make_invoice(
        total=thresholds.ceiling_usd + Decimal("0.01"),
        line_items=[
            LineItem(
                description="x",
                quantity=Decimal("1"),
                unit_price=thresholds.ceiling_usd + Decimal("0.01"),
            )
        ],
        attendees=10,
    )
    assert route(evaluate(over), approve_assessment(), thresholds).route == Route.HUMAN_REVIEW


# --- Confidence gate: low confidence can only downgrade to human.


def test_low_confidence_escalates_even_when_in_policy() -> None:
    decision = route(evaluate(make_invoice()), approve_assessment(confidence=0.5))
    assert decision.route == Route.HUMAN_REVIEW
    assert "AUTONOMY-CONFIDENCE" in decision.cited_rules


@pytest.mark.parametrize(
    "confidence,expected",
    [(0.80, Route.AUTO_APPROVE), (0.79, Route.HUMAN_REVIEW)],
)
def test_confidence_threshold_boundary(confidence: float, expected: Route) -> None:
    assert decide(make_invoice(), approve_assessment(confidence)) == expected


# --- Reject override: deterministic high-severity beats an approve recommendation.


def test_alcohol_only_rejects_despite_approve_recommendation() -> None:
    invoice = make_invoice(
        line_items=[
            LineItem(
                description="Alcohol-only bar tab",
                quantity=Decimal("1"),
                unit_price=Decimal("60"),
            )
        ],
        total=Decimal("60"),
    )
    decision = route(evaluate(invoice), approve_assessment())
    assert decision.route == Route.REJECT
    assert "MEAL-03" in decision.cited_rules


# --- Duplicate short-circuit.


def test_already_processed_routes_duplicate() -> None:
    decision = route(evaluate(make_invoice()), approve_assessment(), already_processed=True)
    assert decision.route == Route.DUPLICATE
    assert "GLOBAL-DUP" in decision.cited_rules


# --- Hard stop below the ceiling still escalates (proves independence of amount).


def test_new_vendor_escalates_below_ceiling() -> None:
    invoice = make_invoice(vendor="NimbusTools", vendor_known=False, total=Decimal("40"))
    decision = route(evaluate(invoice), approve_assessment())
    assert decision.route == Route.HUMAN_REVIEW
    assert "GLOBAL-VENDOR" in decision.cited_rules
