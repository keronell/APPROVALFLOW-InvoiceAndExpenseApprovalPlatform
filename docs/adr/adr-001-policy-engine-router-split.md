# ADR-001 — Deterministic policy engine + router, separated from the agent

- Status: context drafted - decision/consequences pending (author)
- Date: 2026-07-07

## Context

The assignment requires the system to be **provably incapable of auto-approving
above the configured ceiling, even when the agent is forced to recommend
approval** (M12/F10), while still auto-approving the low-risk majority (F6 — no
$0 dodge). An LLM is non-deterministic and can be steered by payload text
(fixture `INV-1013` ships an "Approve me…" note specifically to test this), so
the money decision cannot rest on model output.

What the code does today:

- `services/decision/approvalflow_decision/policy_engine.py` —
  `evaluate(invoice, thresholds)` is a **pure function** (no LLM, no I/O, no
  randomness): FX→USD conversion at fixed rates, math reconciliation
  (GLOBAL-MATH), receipt/vendor/FX/fraud hard stops, per-category caps
  (MEAL-01/02/03, SAAS-01, HW-01/02, TRAVEL-01/02/03). It emits typed
  `PolicyFinding`s and the authoritative **USD amount**.
- `services/decision/approvalflow_decision/agent/` — the Microsoft Agent
  Framework agent returns a **structured, advisory** `AgentAssessment`
  (category, risk, confidence, cited rules, recommendation, reasoning). It has
  no authority; invoice notes are treated as data, never instructions.
- `services/decision/approvalflow_decision/router.py` — `route()` is a **pure
  function**; `auto_approve` is only reachable in the final branch where every
  condition already passed: USD ≤ ceiling ∧ confidence ≥ 0.80 ∧
  category-compliant ∧ no hard stop ∧ no rejection. The ceiling comparison
  reads `evaluation.usd_amount` (engine output), never anything the model
  produced; the agent's `recommendation` field is not read by the router at
  all, and confidence can only *downgrade* an item to human review.
- Enforcement is tested: all 20 shipped fixtures land on their labeled
  `expected.route` even with an over-eager "approve everything" agent
  (`tests/unit/test_fixtures_expected_routes.py`), and a forced
  approve-at-confidence-1.0 on an over-ceiling item still escalates with no
  budget reservation (`tests/unit/test_router.py`).

The alternative shapes considered in the course material: let the agent decide
directly (with prompt-side guardrails), or validate the agent's decision after
the fact. The chosen split ("classifier proposes, router decides") was taught
as the pattern for exactly this volatility boundary.

## Decision

<!-- TODO: write this yourself — you defend this live -->

## Consequences

<!-- TODO: write this yourself — you defend this live -->
