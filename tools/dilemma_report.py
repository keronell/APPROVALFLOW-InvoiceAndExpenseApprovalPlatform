#!/usr/bin/env python3
"""Evidence generator for docs/PRODUCT-DILEMMA.md.

Replays every shipped fixture through the REAL decision pipeline — the
deterministic policy engine + the stub agent assessment (the same one CI and
verify.py use) + the deterministic router — and prints two Markdown tables:

1. Evidence: per-fixture outcome at the shipped posture ($250 / 0.80), with
   the rule(s)/threshold(s) that drove each decision.
2. Threshold sensitivity: the same replay at alternate global ceilings, with
   confidence, category caps, and hard stops unchanged.

Analysis only: thresholds are passed as overrides; the shipped defaults in
services/decision/.../models.py and services/config/.../defaults.py are not
touched.

Usage: python tools/dilemma_report.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from approvalflow_decision import AutonomyThresholds, evaluate, route
from approvalflow_decision.agent.stub_client import assess_invoice
from approvalflow_shared.contracts import InvoiceSubmission, Route

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "Examples" / "sample-invoices.json").read_text(encoding="utf-8"))[
    "fixtures"
]

# INV-1007 only routes `duplicate` against already-processed state (held by
# intake in the live system); modeled here the same way verify.py sequences it.
ALREADY_PROCESSED = {"INV-1007"}

# Fixtures that must NEVER auto-approve regardless of the ceiling: hard stops,
# fraud, math/receipt/vendor/FX violations, the reject case, and the
# adversarial "approve me" note (blocked by the SAAS-01 category cap + ceiling).
NEVER_AUTO = {
    "INV-1005",  # missing receipt (GLOBAL-RECEIPT)
    "INV-1006",  # math mismatch (GLOBAL-MATH)
    "INV-1008",  # fraud pattern + unknown vendor (GLOBAL-FRAUD/VENDOR)
    "INV-1009",  # FX hard stop (GLOBAL-FX)
    "INV-1011",  # unknown vendor below ceiling (GLOBAL-VENDOR)
    "INV-1013",  # adversarial "approve me" note (SAAS-01 cap; agent must not be steered)
    "INV-1015",  # alcohol-only (MEAL-03 reject)
}


def decide(fixture: dict, thresholds: AutonomyThresholds):
    invoice = InvoiceSubmission.model_validate(fixture)
    evaluation = evaluate(invoice, thresholds)
    assessment = assess_invoice(fixture)
    decision = route(
        evaluation,
        assessment,
        thresholds,
        already_processed=fixture["id"] in ALREADY_PROCESSED,
    )
    return evaluation, assessment, decision


def driver(decision, evaluation, assessment, thresholds) -> str:
    """Human-readable 'what drove this route'."""
    if decision.route == Route.AUTO_APPROVE:
        return (
            f"all gates passed (${evaluation.usd_amount} <= ${thresholds.ceiling_usd}, "
            f"conf {assessment.confidence:.2f} >= {thresholds.min_confidence:.2f}, "
            "category-compliant, no hard stop)"
        )
    if decision.cited_rules:
        return ", ".join(decision.cited_rules)
    return decision.reason


def evidence_table(thresholds: AutonomyThresholds) -> str:
    rows = [
        "| id | USD amount | category | ceiling | route | driven by |",
        "|---|---|---|---|---|---|",
    ]
    for fixture in FIXTURES:
        evaluation, assessment, decision = decide(fixture, thresholds)
        marker = " **(adversarial note: did NOT flip)**" if fixture["id"] == "INV-1013" else ""
        rows.append(
            f"| {fixture['id']}{marker} | ${evaluation.usd_amount} | {fixture['category']} "
            f"| ${thresholds.ceiling_usd} | {decision.route} "
            f"| {driver(decision, evaluation, assessment, thresholds)} |"
        )
    return "\n".join(rows)


def sensitivity_table(ceilings: list[str]) -> str:
    rows = [
        "| ceiling | auto-approved | % of fixtures | hard-stop/adversarial breach | notes |",
        "|---|---|---|---|---|",
    ]
    for ceiling in ceilings:
        thresholds = AutonomyThresholds(ceiling_usd=Decimal(ceiling))
        auto: list[str] = []
        breaches: list[str] = []
        for fixture in FIXTURES:
            _, _, decision = decide(fixture, thresholds)
            if decision.route == Route.AUTO_APPROVE:
                auto.append(fixture["id"])
                if fixture["id"] in NEVER_AUTO:
                    breaches.append(fixture["id"])
        pct = round(100 * len(auto) / len(FIXTURES))
        breach = f"YES: {', '.join(breaches)}" if breaches else "no"
        shipped = " **(shipped posture)**" if ceiling == "250" else ""
        rows.append(
            f"| ${ceiling}{shipped} | {len(auto)} of {len(FIXTURES)} "
            f"({', '.join(auto) or 'none'}) | {pct}% | {breach} "
            f"| confidence 0.80, category caps + hard stops unchanged |"
        )
    return "\n".join(rows)


def main() -> None:
    shipped = AutonomyThresholds()
    assert str(shipped.ceiling_usd) == "250" and shipped.min_confidence == 0.80
    print("## Evidence (shipped posture)\n")
    print(evidence_table(shipped))
    print("\n## Sensitivity\n")
    print(sensitivity_table(["100", "250", "500"]))


if __name__ == "__main__":
    main()
