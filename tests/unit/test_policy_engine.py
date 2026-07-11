"""Deterministic policy-engine rule detection (independent of the router)."""

import datetime as dt
from decimal import Decimal

from approvalflow_decision import evaluate
from approvalflow_shared.contracts import InvoiceSubmission


def _evaluate(fixture: dict):
    return evaluate(InvoiceSubmission.model_validate(fixture))


def test_math_mismatch_is_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1006"])
    assert "GLOBAL-MATH" in ev.rule_ids
    assert ev.hard_stops


def test_math_reconciles_for_clean_item(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1001"])
    assert "GLOBAL-MATH" not in ev.rule_ids


def test_fx_conversion_and_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1009"])
    # EUR 1200 * 1.08 = 1296.00
    assert ev.usd_amount == Decimal("1296.00")
    assert "GLOBAL-FX" in ev.rule_ids


def test_missing_receipt_over_25_is_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1005"])
    assert "GLOBAL-RECEIPT" in ev.rule_ids


def test_unknown_vendor_is_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1011"])
    assert "GLOBAL-VENDOR" in ev.rule_ids


def test_fraud_pattern_detected(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1008"])
    assert "GLOBAL-FRAUD" in ev.rule_ids
    assert "GLOBAL-VENDOR" in ev.rule_ids


def test_saas_over_cap_not_compliant(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1018"])
    assert ev.category_compliant is False
    assert "SAAS-01" in ev.rule_ids


def test_hardware_over_cap_is_capital_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1004"])
    assert "HW-02" in ev.rule_ids
    assert ev.hard_stops


def test_travel_over_1500_hard_stop(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1019"])
    assert "TRAVEL-02" in ev.rule_ids


def test_eligible_travel_is_compliant(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1016"])
    assert ev.category_compliant is True
    assert not ev.hard_stops


def test_alcohol_only_is_high_severity(fixtures_by_id: dict) -> None:
    ev = _evaluate(fixtures_by_id["INV-1015"])
    assert "MEAL-03" in ev.rule_ids
    assert ev.rejections


def test_saas_cap_compared_in_usd_not_original_currency() -> None:
    """A €199 SaaS subscription converts to ~$215 (>$200) and must NOT slip
    under the cap on its raw 199 figure."""
    invoice = InvoiceSubmission(
        id="INV-EUR-SAAS",
        submitter="x@northwind.example",
        department="engineering-2026Q2",
        vendor="Figma",
        vendor_known=True,
        invoice_number="EU-1",
        currency="EUR",
        category="saas",
        line_items=[{"description": "seats", "quantity": 1, "unit_price": "199"}],
        tax_amount=Decimal("0"),
        total=Decimal("199"),
        receipt_present=True,
        date=dt.date(2026, 5, 12),
    )
    ev = evaluate(invoice)
    assert ev.usd_amount == Decimal("214.92")  # 199 * 1.08
    assert "SAAS-01" in ev.rule_ids
    assert ev.category_compliant is False


def test_unknown_currency_is_hard_stop() -> None:
    """A currency with no configured FX rate is unconvertible → human review,
    never a 1:1 guess that could auto-approve."""
    invoice = InvoiceSubmission(
        id="INV-JPY",
        submitter="x@northwind.example",
        department="engineering-2026Q2",
        vendor="Figma",
        vendor_known=True,
        invoice_number="JP-1",
        currency="JPY",
        category="saas",
        line_items=[{"description": "seats", "quantity": 1, "unit_price": "100"}],
        tax_amount=Decimal("0"),
        total=Decimal("100"),
        receipt_present=True,
        date=dt.date(2026, 5, 12),
    )
    ev = evaluate(invoice)
    assert "GLOBAL-FX-UNKNOWN" in ev.rule_ids
    assert ev.hard_stops


def test_fraud_needs_unknown_vendor() -> None:
    """A round-number, no-detail, weekend submission from a KNOWN vendor is not
    flagged as fraud — the pattern requires a new vendor."""
    invoice = InvoiceSubmission(
        id="INV-KNOWN-ROUND",
        submitter="x@northwind.example",
        department="engineering-2026Q2",
        vendor="Dell",
        vendor_known=True,
        invoice_number="K-1",
        currency="USD",
        category="other",
        line_items=[],
        tax_amount=Decimal("0"),
        total=Decimal("5000"),
        receipt_present=True,
        date=dt.date(2026, 5, 17),  # a Sunday
    )
    ev = evaluate(invoice)
    assert "GLOBAL-FRAUD" not in ev.rule_ids
