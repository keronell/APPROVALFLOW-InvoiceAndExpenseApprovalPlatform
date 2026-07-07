# Future work — evolving the autonomy posture

Two candidate directions for resolving the dilemma more deeply than a fixed
posture (owner's ideas, captured here for future implementation). Both share
one non-negotiable integration rule: **nothing learned and nothing
LLM-generated ever gains final authority over money.** The deterministic
router (`services/decision/approvalflow_decision/router.py`) remains the only
component that decides; these ideas improve its *inputs* — the numbers it
enforces and the confidence it gates on. Anything else would break the
provable ceiling (M12/F10) that the current tests guarantee.

They combine cleanly: *the council calibrates confidence per item; history
evolves the thresholds over time; the deterministic router remains the only
thing that ever touches money.*

---

## Idea 1 — History-driven posture: learn the thresholds from human decisions

**Concept.** Every escalated item that a human approves or rejects is a
labeled example of "the posture was too tight/right here." Aggregate that
history and use it to **propose** threshold changes — never to decide
individual items.

**What already exists to build on:**

- `escalation.resolved` events carry `action` (approve/reject/send_back) and
  `usd_amount`; the reporting service already consumes them and keeps a full
  per-correlation-id audit trail (`services/reporting/`).
- Thresholds are already runtime-changeable with no redeploy
  (`PUT /config/thresholds`, F7/M13) — the exact lever a proposal would pull.
- `tools/dilemma_report.py` already replays the fixture set against arbitrary
  threshold overrides — the same harness can score a *proposed* posture before
  a controller applies it.

**Sketch of the loop (posture advisor):**

1. A small analysis job (extension of reporting, or a `tools/` script) groups
   human decisions by category / vendor-known / amount band.
2. Where humans approve near-unanimously (e.g. ≥95% approval of known-vendor
   hardware $250–$500 over N≥ some floor), emit a **proposal**: "raise
   `hardware` autonomy to $X — projected escalation reduction Y%, based on Z
   decisions."
3. Replay the proposal against the labeled fixture set (regression floor: the
   20 shipped fixtures must keep their expected routes; zero hard-stop
   breaches).
4. A **controller reviews and applies** it via the existing config endpoint.
   The router never changes; only the numbers it enforces do.

**Known weaknesses (state them when defending):**

- *Cold start* — no real history at launch; only 20 fixtures.
- *Selection bias* — auto-approved items never receive human labels, so the
  system only learns about the escalated band; lowering a threshold is
  better-evidenced than raising one.
- *Gaming/drift* — submitters/vendors can adapt to learned patterns; hard
  stops (vendor, fraud, math, receipt, FX) must stay non-negotiable and
  outside the learned surface.

**Rough effort:** ~1 day for the advisor report + proposal format; more if the
apply step gets its own UI.

---

## Idea 2 — LLM council: agreement as calibrated confidence

**Concept.** Replace the single small model with 2–3 heterogeneous small
models (e.g. `qwen2.5:3b` + `llama3.2:3b` + one more) that assess the same
invoice **concurrently**. Confidence stops being one model's self-report and
becomes **measured agreement**: unanimous on category/risk → high confidence;
split → low confidence → the existing 0.80 gate escalates to a human. This is
the *concurrent orchestration / ensemble voting* pattern from the AI course.

**What already exists to build on:**

- The provider abstraction (`agent/client.py`) builds chat clients from
  config; a council is N clients instead of one.
- `DecisionAgent` (`agent/assessor.py`) is the single seam the router sees —
  a `CouncilDecisionAgent` returning one aggregated `AgentAssessment` changes
  nothing downstream: the router, ceiling, and hard stops are untouched.
- CI/verification keep using the deterministic stub; the council is a
  live-mode concern only.

**Sketch:**

1. Fan out the same structured-output request to N models (MAF supports
   concurrent orchestration; plain `asyncio.gather` over N `Agent.run` calls
   also works).
2. Aggregate: majority vote on `category` and `recommendation`; union of
   `policy_violations`/`fraud_signals` (safety-biased); confidence =
   agreement ratio × mean self-confidence (or simply the agreement ratio).
3. Emit one `AgentAssessment`; everything downstream is unchanged.

**Known weaknesses (state them when defending):**

- It strengthens *calibration*, not *safety* — the router already ignores
  recommendations, and a prompt-injection attempt could steer all members the
  same way; the deterministic defenses stay the real guarantee.
- ×N latency and cost; three local models on CPU will visibly slow the demo.
- Small models fail in correlated ways — agreement can be confidently wrong;
  heterogeneous model families reduce but don't remove this.

**Rough effort:** ~1 day behind `DecisionAgent` (config: `LLM_COUNCIL=model1,
model2,…`), plus demo-hardware reality-checking.

---

## Suggested sequencing

1. **Idea 1 first** — cheapest, feeds the PRODUCT-DILEMMA justification
   directly, and uses data the system already emits.
2. **Idea 2 second** — as a bonus/`dev`-branch item, or documented as designed
   future work if demo hardware makes three live models impractical.
