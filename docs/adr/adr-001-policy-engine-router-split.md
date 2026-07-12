# ADR-001 - Deterministic policy engine + router, separated from the agent

- Status: accepted (2026-07-12)
- Date: 2026-07-07

## Context

The assignment requires the system to be **provably incapable of auto-approving
above the configured ceiling, even when the agent is forced to recommend
approval** (M12/F10), while still auto-approving the low-risk majority (F6 - no
$0 dodge). An LLM is non-deterministic and can be steered by payload text
(fixture `INV-1013` ships an "Approve me…" note specifically to test this), so
the money decision cannot rest on model output.

What the code does today:

- `services/decision/approvalflow_decision/policy_engine.py` -
  `evaluate(invoice, thresholds)` is a **pure function** (no LLM, no I/O, no
  randomness): FX→USD conversion at fixed rates, math reconciliation
  (GLOBAL-MATH), receipt/vendor/FX/fraud hard stops, per-category caps
  (MEAL-01/02/03, SAAS-01, HW-01/02, TRAVEL-01/02/03). It emits typed
  `PolicyFinding`s and the authoritative **USD amount**.
- `services/decision/approvalflow_decision/agent/` - the Microsoft Agent
  Framework agent returns a **structured, advisory** `AgentAssessment`
  (category, risk, confidence, cited rules, recommendation, reasoning). It has
  no authority; invoice notes are treated as data, never instructions.
- `services/decision/approvalflow_decision/router.py` - `route()` is a **pure
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

I adopt "classifier proposes, router decides":

The agent (MAF) returns only an advisory AgentAssessment - it holds no authority over money.
The pure policy_engine.evaluate() computes the authoritative USD amount and all hard stops, independently of the LLM.
The pure route() function makes the binding decision; auto-approve is reachable only in the final branch where every condition has passed.
The ceiling check reads the engine's usd_amount, never any model output; confidence can only downgrade an item to human review.

## Consequences

Positive. Because the money math lives in pure functions (policy_engine.evaluate and route) and never in the model, the autonomy ceiling is a structural guarantee rather than a statistical hope: the auto-approve branch is unreachable while evaluation.usd_amount exceeds the ceiling, and the router never reads the agent's recommendation at all. This kills prompt injection as a class - INV-1013's "Approve me" note enters only as lowercased data that can add scrutiny, never remove it - and the guarantee is pinned down by tests (test_router.py, test_fixtures_expected_routes.py) that hold even against an "approve everything" agent. A deterministic money path is also cheap to trust going forward: the agent can be refactored or swapped freely while that regression floor stays green.

Trade-offs. The split costs me a duplicated representation of policy - category caps live in the engine and in the agent's prompt - so a threshold change touches two places and can skew; I accept that because the engine is authoritative and the prompt copy is only advice, meaning drift degrades recommendation quality but never safety. I also make confidence strictly conservative: it can only downgrade an item to human review, never accelerate an approval. That sends some correct-but-hesitant items to a human, but the asymmetry is deliberate - a wrong auto-approval spends money incorrectly, while a needless escalation only spends a reviewer's minute, and those errors are not equal in cost.

Risks and follow-ups. Thresholds are served hot by the config service with AutonomyThresholds defaults mirroring policy.md; if a poisoned or invalid value arrives, validation fails and the system falls back to the safe defaults rather than converting totals to $0 - so the mechanism fails toward more review. The residual exposure is a value that is valid but wrong (say, a ceiling raised to $2,500): the engine enforces it faithfully, so correctness there rests on whoever sets the number, not on the code. Finally, the agent boundary fails loud, not silent: agent/init.py checks for the MAF symbols it builds against and raises ImportError at import if any go missing, so a breaking framework upgrade crashes at startup instead of silently defaulting a decision at request time.
