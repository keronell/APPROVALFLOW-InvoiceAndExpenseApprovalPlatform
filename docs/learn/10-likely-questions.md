# Likely questions

Every real decision documented in `docs/adr/*.md` and `docs/PRODUCT-DILEMMA.md`,
reframed as the question an interviewer would actually ask, with a factual
answer key — not a script. Answer these in your own words; the facts and
file/line pointers are here so you can check yourself.

---

## ADR-001 — Policy engine + router split

**"What actually stops the agent from approving something above the ceiling,
even if you told it to?"**
Facts: `route()` (`router.py`) is a pure function. The ceiling check compares
`evaluation.usd_amount` — computed by `policy_engine.evaluate()`, no LLM
involved — against `thresholds.ceiling_usd`. `assessment.recommendation` is
never read by `route()` at all; grepping the function body, the only
`assessment.` attribute it ever touches is `.confidence`.
Evidence: `test_forced_approve_over_ceiling_still_escalates` hand-builds an
`AgentAssessment(confidence=1.0, recommendation=APPROVE)` on a $5,000 item and
it still escalates; `test_fixtures_expected_routes.py` replays all 20 shipped
fixtures against a simulated "approve everything confidently" agent and every
one still lands on its labeled route.

**"Isn't keeping policy logic in both the engine and the agent's prompt a
maintenance risk?"**
Facts: ADR-001's Trade-offs section names this directly — category caps live
in `policy_engine.py` *and* in the agent's `fetch_policy` tool snippets; a
threshold change touches two places. The ADR's position: acceptable because
the engine is authoritative and the agent's copy is advisory-only, so drift
degrades recommendation *quality* (more escalations than strictly necessary),
never *safety*.

**"Why can confidence only ever downgrade a decision, never push one toward
approval?"**
Facts: in `router.py`, `low_confidence` is one of several independent
blockers that can each force `human_review`; there's no path where high
confidence grants anything on its own. ADR-001's stated reasoning: a wrong
auto-approval spends money incorrectly; a needless escalation only costs a
reviewer a minute — the two error types are treated as unequal in cost, on
purpose.

**"What's the test evidence this actually holds, not just the code?"**
Facts: `tests/unit/test_router.py` and `tests/unit/test_fixtures_expected_routes.py`
— `CLAUDE.md` states explicitly that any change touching this area must keep
both green.

---

## ADR-002 — Saga vs. two-phase commit

**"Why not two-phase commit for the payment flow?"**
Facts: ADR-002's Context: the money path spans RabbitMQ, Redis, and two
independently-stored services (payment owns budget/reservation/payment
records; intake's submitter-visible status is updated only via events) with
no shared transaction coordinator to enrol as a 2PC resource manager. Its
blocking prepare phase would also couple the availability of services this
architecture otherwise keeps deliberately decoupled.

**"How do you guarantee no double payment and no orphaned reservation,
concretely?"**
Facts: `payment_workflow`'s compensation path — a `PaymentDeclined` from
`pay_activity` triggers `release_activity` (restores the budget, marks the
reservation `RELEASED`) then `fail_activity`. A reserve *refusal*
(insufficient budget) skips straight to `fail_activity` — nothing was
reserved, so nothing needs releasing.
Evidence: `tools/verify.py`'s `journey_payment_failure_compensation`
(`INV-1012`) reads the department budget before, forces a payment failure via
the documented `test.failPayment` hook, and asserts the budget after is
identical.

**"How do you stop two concurrent approvals from overspending the same
budget?"**
Facts: ETag first-write concurrency on the `budget::{department}` key
(`DaprIO.try_execute_transaction`, retried up to 8 times, re-reading
available budget fresh each attempt).
Evidence: `test_concurrent_reservations_never_overspend` (unit, two $600
reservations via `asyncio.gather` against a $1,000 budget) and
`guard_concurrent_budget` in `tools/verify.py` (end-to-end, `INV-1014A/B`).

**"What happens if the compensation step itself fails?"**
Facts: ADR-002's own Risks section names this as a real, unresolved gap —
compensation can fail and would strand a reservation until retried; a
"poisoned or permanently-failing compensation would need operator attention."
Verified in the code: no `call_activity` in `payment/workflow.py` passes an
explicit retry policy, and the `try/except` around `pay_activity` does not
wrap the compensating calls themselves — a failure inside `release_activity`
propagates uncaught out of the orchestrator.

---

## ADR-003 — Dapr as the integration layer

**"Why lean on Dapr this heavily instead of hand-rolled httpx calls, a
RabbitMQ client library, redis-py, and env-var secrets?"**
Facts: ADR-003's Decision — uniform use across invocation, pub/sub, state,
and secrets (plus Workflow) keeps services thin and portable: no hardcoded
peer URLs, no broker/store credentials embedded in code, and swapping the
backing technology becomes a component-yaml change, not a code change.
`keyPrefix` enforces per-service state ownership at the infrastructure layer
for free.

**"What did you deliberately *not* use from Dapr, and why?"**
Facts: the *configuration* building block. Thresholds instead live in plain
state, served over service invocation by `config`, so there's one
runtime-mutation path (`PUT /config/thresholds`) rather than two systems that
could disagree. An unused `configuration.redis` component briefly existed and
was explicitly removed "to keep the component set honest" (`plan.md`'s open
items).

**"What's the operational cost of this choice?"**
Facts: ADR-003's Trade-offs — every service runs beside its own `daprd`
sidecar; Workflow forces `placement` and `scheduler` into the compose file;
the sidecar sits in the critical path, so a slow or down `daprd` degrades
invocation and pub/sub for whatever service it belongs to.

---

## ADR-004 — Config fallback fails safe

**"What happens to a decision when the config service is unreachable?"**
Facts: `fetch_thresholds` (`config_client.py:20`) catches *any* exception —
network failure, validation error, empty response — logs one
`thresholds_fetch_failed` warning, and returns hardcoded
`AutonomyThresholds()` defaults ($250 / 0.80). The pipeline never crashes,
never blocks, never silently loosens.

**"Why fail-safe-to-defaults instead of failing closed (escalate everything)
or caching last-known-good?"**
Facts: ADR-004's Decision rejects both alternatives explicitly — fail-closed
"turns a config blip into a full autonomy outage that floods human review";
last-known-good caching "adds a staleness/invalidation surface and could
itself serve an out-of-date loosened value"; crash-and-retry "converts a
recoverable degradation into an availability failure of the money path."

**"Is there a scenario where this fallback is actually unsafe?"**
Facts: ADR-004's own stated caveat — the fallback is safe *relative to the
shipped posture*, not relative to a stricter runtime override. If a
controller had tightened the ceiling at runtime (say to $100), an outage
reverts decisions to the looser $250 default until config is reachable
again — and that reversion is invisible to the submitter, only a log
warning, unless that line is wired to monitoring.

---

## PRODUCT-DILEMMA.md — the autonomy posture

**"What's your autonomy posture, and why these specific numbers?"**
Facts: $250 ceiling, 0.80 confidence — unchanged from `policy.md` §6
defaults. At $250, 4 of 20 shipped fixtures auto-approve (20%). The
sensitivity replay (`tools/dilemma_report.py`) shows raising to $500 adds
**zero** additional auto-approvals on this dataset — the next candidates up
are `INV-1013` ($300, blocked by the `SAAS-01` category cap) and `INV-1010`
($480, blocked by low agent confidence on an ambiguous bundle).

**"Isn't the ceiling the main safety control here? What's actually stopping
unsafe approvals?"**
Facts: `PRODUCT-DILEMMA.md` states this directly — the ceiling is *not* the
binding safety control. Across the tested $100–$500 range, no hard-stop or
adversarial case (fraud `INV-1008`, unknown vendor `INV-1011`, FX `INV-1009`,
math mismatch `INV-1006`, missing receipt `INV-1005`, the "approve me" note
`INV-1013`, the alcohol reject `INV-1015`) ever gets through — all blocked by
rules independent of the ceiling. The ceiling's actual job is narrower:
bounding how large an in-policy, low-risk item may be before a human still
sees it.

**"Why not set the ceiling to $0 and escalate everything — isn't that
maximally safe?"**
Facts: the assignment explicitly disallows this — F6: *"You may not dodge the
dilemma by escalating everything — a $0 ceiling trivially passes the safety
tests but defeats the product."* `tools/verify.py`'s `guard_no_zero_dodge`
checks the configured ceiling is `> 0`; `test_at_least_two_no_human_auto_approvals`
asserts at least 2 fixtures genuinely auto-approve with no human.

**"How would you defend a different number if asked to justify it live?"**
Facts: `python tools/dilemma_report.py` reproduces the whole evidence and
sensitivity analysis on demand, replaying every fixture at an arbitrary
ceiling override without touching the shipped code defaults. On this
dataset, lowering to $100 costs exactly one real auto-approval
(`INV-1017`, a $180 in-policy keyboard) for no safety gain.

---

## FUTURE-WORK.md — evolving the posture

**"If you had more time, how would you move past fixed threshold numbers?"**
Facts: two sketched directions, both keeping the deterministic router as the
only thing that ever touches money. (1) *History-driven thresholds* —
aggregate human approve/reject decisions on escalated items (already
captured via `escalation.resolved` + reporting's audit trail) into
**proposed** threshold changes, replayed against the 20-fixture regression
floor before a controller applies them through the existing
`PUT /config/thresholds`. (2) *LLM council* — 2–3 heterogeneous small models
assess concurrently; confidence becomes measured **agreement** rather than
one model's self-report; still produces exactly one `AgentAssessment` the
router treats identically to today.

**"What are the known weaknesses of the history-driven idea?"**
Facts: `FUTURE-WORK.md` names three — cold start (only 20 fixtures exist at
launch); selection bias (auto-approved items never receive a human label, so
the system only ever learns about the escalated band — lowering a threshold
is better-evidenced than raising one); gaming/drift (submitters could adapt
to learned patterns, so hard stops must stay non-negotiable and outside the
learned surface).

---

## Beyond the docs — from the code itself

A handful more, grounded directly in the code rather than the ADRs, because
they're exactly the kind of sharp follow-up an interviewer asks right after
one of the questions above.

**"Why `agent-framework-core` + `agent-framework-openai` instead of the
`agent-framework` meta package?"**
Facts: the meta package expands to `agent-framework-core[all]`, whose
beta/rc extras make an unconstrained `pip install` backtrack until it gives
up. `services/decision/constraints.txt` pins the entire known-good
resolution, generated *inside* the target image so platform-selected
dependencies (`uvloop`, `httptools`) are pinned too — see the file's own
header for the regeneration recipe.

**"What actually stops a submitter from just claiming `vendorKnown: true` for
a brand-new vendor?"**
Facts: the `GLOBAL-VENDOR` hard stop in `policy_engine.py` trusts
`invoice.vendor_known` — a submitter-supplied field — directly. The agent's
independent `lookup_vendor` tool result is never read back into the
deterministic path; `route()` never sees it. Other hard stops (fraud
pattern, math, receipt, FX) are independent and could still catch a bad
submission, but "known vendor" itself is currently self-attested with no
cross-check.

**"Why doesn't `approval` use Dapr Workflow like `decision` and `payment`
do?"**
Facts: it's a single atomic state transition (claim one escalation), not a
multi-step orchestration — the codebase hand-rolls equivalent safety with
ETag CAS plus a side-effects lease with TTL-based crash recovery instead
(`04-approval.md`).

**"Where's the one place a crash could genuinely *lose* a submission, not
just delay it?"**
Facts: `intake/service.py`, between `store.put(record)` and
`publisher.publish(...)` — but the `202` response (carrying the tracking id)
is only returned after both complete, so the caller never receives a
tracking id to even ask about the orphaned record in that exact window. Not
an M9/M10 double-payment risk — the item never progressed — but a real,
acknowledged availability gap (`01-intake.md`; the outbox pattern, N3, was
not attempted).

**"How do you know the four worked journeys actually work, not just that
unit tests pass?"**
Facts: `tools/verify.py` drives all four through the real HTTP surface
against a live `docker compose` stack (the deterministic stub model — no
network, no rate limits), 11 checks total — including one that actually
restarts the `decision` container mid-flight
(`journey_escalate_and_resume`) to prove the HITL pause is durable in fact,
not just in theory. It runs in CI on every push
(`.github/workflows/ci.yml`'s `verify` job).
