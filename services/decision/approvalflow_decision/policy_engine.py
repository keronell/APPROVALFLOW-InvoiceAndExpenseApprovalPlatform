"""Deterministic policy engine.

Pure functions over an :class:`InvoiceSubmission` and
:class:`AutonomyThresholds`. No LLM, no I/O, no randomness — the same input
always yields the same findings. This is what makes the autonomy ceiling
provable (M12/F10): every money- and hard-stop-relevant fact is computed here,
independently of the agent.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from approvalflow_shared.contracts import ExpenseCategory, InvoiceSubmission

from .models import AutonomyThresholds, PolicyEvaluation, PolicyFinding, Severity

_CENTS = Decimal("0.01")


def _to_cents(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _usd_amount(invoice: InvoiceSubmission, thresholds: AutonomyThresholds) -> Decimal:
    """Convert the total to USD at the submission-date rate (GLOBAL-FX)."""
    rate = thresholds.fx_rates.get(invoice.currency.upper(), Decimal("1"))
    return _to_cents(invoice.total * rate)


def _line_items_total(invoice: InvoiceSubmission) -> Decimal:
    return sum(
        (item.quantity * item.unit_price for item in invoice.line_items),
        Decimal("0"),
    )


def _text(invoice: InvoiceSubmission) -> str:
    """Lowercased free text — used ONLY for deterministic keyword red flags,
    never for authority. The router never reads free text (prompt-injection
    defense): keywords here can only *add* constraints, never remove them."""
    parts = [invoice.notes or ""]
    parts += [item.description for item in invoice.line_items]
    return " ".join(parts).lower()


def evaluate(
    invoice: InvoiceSubmission, thresholds: AutonomyThresholds | None = None
) -> PolicyEvaluation:
    """Apply every deterministic rule from policy.md and return the findings."""
    thresholds = thresholds or AutonomyThresholds()
    findings: list[PolicyFinding] = []
    usd = _usd_amount(invoice, thresholds)
    text = _text(invoice)

    # --- GLOBAL-MATH: line items + tax must reconcile to total (original currency).
    reconciled = _to_cents(_line_items_total(invoice) + invoice.tax_amount)
    if reconciled != _to_cents(invoice.total):
        findings.append(
            PolicyFinding(
                rule_id="GLOBAL-MATH",
                severity=Severity.HARD_STOP,
                message=(
                    f"Line items + tax ({reconciled}) do not reconcile to "
                    f"total ({_to_cents(invoice.total)})."
                ),
            )
        )

    # --- GLOBAL-RECEIPT: receipt required for any expense over $25.
    if usd > thresholds.receipt_required_over_usd and not invoice.receipt_present:
        findings.append(
            PolicyFinding(
                rule_id="GLOBAL-RECEIPT",
                severity=Severity.HARD_STOP,
                message=(
                    f"Receipt required for expenses over "
                    f"${thresholds.receipt_required_over_usd}."
                ),
            )
        )

    # --- GLOBAL-VENDOR: new/unknown vendor is always human-reviewed.
    if not invoice.vendor_known:
        findings.append(
            PolicyFinding(
                rule_id="GLOBAL-VENDOR",
                severity=Severity.HARD_STOP,
                message=f"New/unknown vendor '{invoice.vendor}' requires human review.",
            )
        )

    # --- GLOBAL-FX: foreign-currency item above the ceiling or over $1,000.
    if invoice.currency.upper() != "USD" and (
        usd > thresholds.fx_hard_stop_usd or usd > thresholds.ceiling_usd
    ):
        findings.append(
            PolicyFinding(
                rule_id="GLOBAL-FX",
                severity=Severity.HARD_STOP,
                message=f"Foreign-currency item converts to ${usd}; FX hard stop.",
            )
        )

    # --- GLOBAL-FRAUD: combined pattern (round number + new vendor + no detail/off-hours).
    round_number = invoice.total >= Decimal("1000") and invoice.total % Decimal("1000") == 0
    no_detail = len(invoice.line_items) <= 1 and all(
        item.quantity == 1 for item in invoice.line_items
    )
    off_hours = invoice.date.weekday() >= 5  # Saturday/Sunday
    if not invoice.vendor_known and round_number and (no_detail or off_hours):
        signals = ["round-number amount", "new vendor"]
        if no_detail:
            signals.append("no line-item detail")
        if off_hours:
            signals.append("off-hours submission")
        findings.append(
            PolicyFinding(
                rule_id="GLOBAL-FRAUD",
                severity=Severity.HARD_STOP,
                message="Fraud-pattern signals: " + ", ".join(signals) + ".",
            )
        )

    # --- Category-specific rules → also decide category_compliant.
    category_compliant = _evaluate_category(invoice, thresholds, usd, text, findings)

    return PolicyEvaluation(
        usd_amount=usd,
        category=invoice.category,
        category_compliant=category_compliant,
        findings=findings,
    )


def _evaluate_category(
    invoice: InvoiceSubmission,
    thresholds: AutonomyThresholds,
    usd: Decimal,
    text: str,
    findings: list[PolicyFinding],
) -> bool:
    """Apply the per-category caps (§1-4). Returns whether the item is
    policy-compliant for its category (a precondition for auto-approval)."""
    category = invoice.category

    if category == ExpenseCategory.MEALS:
        # MEAL-03: alcohol-only receipts are not reimbursable → reject.
        if "alcohol" in text:
            findings.append(
                PolicyFinding(
                    rule_id="MEAL-03",
                    severity=Severity.HIGH,
                    message="Alcohol-only receipt is not reimbursable.",
                )
            )
            return False
        # MEAL-01: attendee count is required.
        if invoice.attendees is None:
            findings.append(
                PolicyFinding(
                    rule_id="MEAL-01",
                    severity=Severity.HARD_STOP,
                    message="Meal submission is missing an attendee count.",
                )
            )
            return False
        # MEAL-02: client entertainment over $500 needs a client name + justification.
        # The submission schema carries no structured client-name field, so any
        # client-entertainment spend over the threshold is treated as missing info.
        if "client" in text and invoice.total > thresholds.client_entertainment_over_usd:
            findings.append(
                PolicyFinding(
                    rule_id="MEAL-02",
                    severity=Severity.HARD_STOP,
                    message="Client entertainment over $500 needs a client name and justification.",
                )
            )
            return False
        # MEAL-01: per-attendee cap.
        per_attendee = usd / Decimal(invoice.attendees) if invoice.attendees else usd
        if per_attendee > thresholds.meal_per_attendee_usd:
            findings.append(
                PolicyFinding(
                    rule_id="MEAL-01",
                    severity=Severity.INFO,
                    message=(
                        f"Meal is ${_to_cents(per_attendee)}/attendee, over the "
                        f"${thresholds.meal_per_attendee_usd} limit."
                    ),
                )
            )
            return False
        return True

    if category == ExpenseCategory.SAAS:
        # SAAS-01: subscriptions eligible up to $200/month.
        if invoice.total > thresholds.saas_monthly_cap_usd:
            findings.append(
                PolicyFinding(
                    rule_id="SAAS-01",
                    severity=Severity.INFO,
                    message=(
                        f"SaaS ${invoice.total}/mo exceeds the "
                        f"${thresholds.saas_monthly_cap_usd}/mo cap."
                    ),
                )
            )
            return False
        return True

    if category == ExpenseCategory.HARDWARE:
        # HW-02: hardware over $1,000 is a Capital expense → always human.
        if invoice.total > thresholds.hardware_cap_usd:
            findings.append(
                PolicyFinding(
                    rule_id="HW-02",
                    severity=Severity.HARD_STOP,
                    message=(
                        f"Hardware over ${thresholds.hardware_cap_usd} is "
                        f"Capital → human review."
                    ),
                )
            )
            return False
        return True  # HW-01 eligible

    if category == ExpenseCategory.TRAVEL:
        # TRAVEL-03: first/business class always requires approval.
        if "first class" in text or "business class" in text:
            findings.append(
                PolicyFinding(
                    rule_id="TRAVEL-03",
                    severity=Severity.HARD_STOP,
                    message="First/business-class travel always requires approval.",
                )
            )
            return False
        # TRAVEL-02: any single travel expense over $1,500 → manager approval.
        if invoice.total > thresholds.travel_single_over_usd:
            findings.append(
                PolicyFinding(
                    rule_id="TRAVEL-02",
                    severity=Severity.HARD_STOP,
                    message=(
                        f"Travel over ${thresholds.travel_single_over_usd} "
                        f"requires manager approval."
                    ),
                )
            )
            return False
        return True  # TRAVEL-01 eligible

    # OTHER: no specific cap. Compliance leans on the ceiling + agent confidence.
    return True
