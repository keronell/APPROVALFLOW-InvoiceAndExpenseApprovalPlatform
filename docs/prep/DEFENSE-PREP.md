# Presentation defense prep — verification notes

Personal rehearsal material, not a graded deliverable. Every claim below was
checked against the working tree as of 2026-07-20, not taken on the source
documents' word — by direct code inspection, `grep`, or actual execution
(`python tools/dilemma_report.py` and the full `pytest` suite, both run in a
scratch venv built from `services/decision/constraints.txt`; 168 tests green,
`dilemma_report.py`'s output diffed against `PRODUCT-DILEMMA.md` row by row).
Facts only throughout — no scripted answers. You fill those in.

---

## Cross-document consistency flags

Things that would sound contradictory quoted back-to-back live, even though
each individual statement is true.

1. **ADR-004: "never widen the ceiling" vs. "could run looser than
   intended."** Decision section: *"an outage can never widen the autonomy
   ceiling."* Risks section: *"a prolonged outage could quietly run the
   system looser than an operator intended."* Reconciliation (already written
   in the ADR's own Trade-offs section): these are two different reference
   frames. Never loosens past the **shipped defaults** ($250/0.80). Can
   loosen past whatever a controller had **tightened it to at runtime** via
   `PUT /config/thresholds` — the fallback is safe relative to what shipped,
   not relative to the last human intent.

2. **ADR-002 and ADR-003 share the same inaccurate claim, not a
   contradiction between them.** Both attribute payment's no-overspend
   guarantee to `DaprIO.try_save_state, retried on 409/412`. The actual
   mechanism (`budget_dapr.py`'s `commit_budget_and_reservation`) is
   `DaprIO.try_execute_transaction` — a single atomic transaction covering
   both the budget key and the reservation key together. The two ADRs agree
   with each other; they're both wrong about the code in the same way. If
   asked to go deeper starting from `ARCHITECTURE.md`'s vaguer, *accurate*
   phrasing ("ETag first-write concurrency, retried on conflict" — no
   function named), don't reach for the ADRs' specific function name.

3. **No direct contradiction found between any two documents on a
   load-bearing fact.** $250/0.80 is consistent everywhere it appears (ADR-004,
   PRODUCT-DILEMMA.md, ARCHITECTURE.md). Everything else flagged below is
   inaccurate relative to the *code*, consistently, not inconsistently
   between docs.

---

## docs/adr/README.md

**Confirmed:** all 4 ADRs are genuinely dated `accepted (2026-07-12)` as the
table states. The "further decisions worth an ADR if time allows" list
(stack choice, RabbitMQ, nginx, the autonomy posture itself) is accurate —
none of those have a dedicated ADR file today.

**Stale/inaccurate:** none found.

---

## ADR-001 — Policy engine + router split

**Confirmed:**
- `route()`'s only `assessment.` attribute access, anywhere in the function
  body, is `.confidence` — verified by direct inspection of `router.py`.
  `assessment.recommendation` is never read.
- `test_forced_approve_over_ceiling_still_escalates` and
  `test_fixtures_expected_routes.py`'s full-suite replay both exist and pass
  as described.
- The prompt-injection defense claim (notes treated as data) matches
  `policy_engine.py`'s `_text()` docstring and the agent's system
  instructions.

**Stale/inaccurate:**
- *Precision nit, not quite wrong:* the ADR states "confidence ≥ 0.80" as
  prose, reading like a fixed constant. It's actually `thresholds.min_confidence`
  — a runtime-configurable field via `PUT /config/thresholds`, currently at
  its default value of 0.80. True today; not hardcoded the way the sentence
  implies.
- *Overclaimed test attribution:* the ADR cites `tests/unit/test_router.py`
  as proof that a forced-approve-at-1.0 item "still escalates with **no
  budget reservation**." The actual test
  (`test_forced_approve_over_ceiling_still_escalates`) asserts only
  `decision.route == Route.HUMAN_REVIEW` and the cited rule — it imports
  nothing from payment/budget and makes no assertion about reservations. The
  no-reservation fact is real (it follows from `payment.requested` only
  being published for `auto_approve`/human-approved routes in
  `decision/service/workflow.py`), but this specific cited test doesn't
  check it directly.

**Questions:**
- "Where does confidence actually come from, and why should anyone trust
  it?" — Facts: `AgentAssessment.confidence` is raw LLM structured output (or
  the stub's category heuristic, or a hardcoded `0.0` fail-safe), with zero
  deterministic cross-check anywhere in `decision_core.py`/`router.py`. Only
  transformation applied: a percent→fraction unit-normalization
  `field_validator` in `models.py`. `FUTURE-WORK.md` Idea 2 already states in
  its own words that confidence today is "one model's self-report."
- "Two places carry the same policy rules — the engine and the agent's
  prompt snippets. How would you catch drift between them today?" — Facts:
  ADR-001's own Trade-offs section names the duplication (category caps live
  in `policy_engine.py` *and* `agent/tools.py`'s `_POLICY_SNIPPETS`) as an
  accepted cost; no automated check comparing the two was found.
- "Is 0.80 hardcoded?" — Facts: no — `AutonomyThresholds.min_confidence`,
  served by `config`, bounded `[0,1]`, mutable at runtime.
- "Does `test_router.py` actually prove no payment reservation happens on a
  forced-approve-over-ceiling item?" — Facts: no, see Stale/inaccurate above.

---

## ADR-002 — Saga vs. two-phase commit

**Confirmed:**
- Compensation path exactly as described:
  `pay_activity` raises `PaymentDeclined` → `release_activity`
  (`BudgetLedger.release`, restores budget, reservation → `RELEASED`) →
  `fail_activity` (`payment.failed` + `item.status-changed(payment_failed)`).
  A reserve refusal skips straight to `fail_activity` — verified against
  current `payment/workflow.py`.
- `tools/verify.py`'s `journey_payment_failure_compensation` (`INV-1012`)
  does exactly what's described: budget read before, forced failure via
  `test.failPayment`, budget read after, asserted identical.
- Idempotency claims (workflow instance id = tracking id; individually
  idempotent activities) verified against `payment/app.py`'s
  `on_payment_requested` and `budget.py`'s reserve/release short-circuits.

**Stale/inaccurate:**
- Both the Context and Decision sections state the no-overspend guarantee
  rests on `DaprIO.try_save_state, retried on 409/412`. The actual code path
  — `BudgetLedger.reserve()`/`.release()` → `commit_budget_and_reservation`
  → `budget_dapr.py` — calls **`DaprIO.try_execute_transaction`**, a single
  atomic transaction covering *both* the `budget::{department}` key
  (ETag-conditional) and the `reservation::{tracking_id}` key together.
  `try_save_state` is real and heavily used elsewhere (approval's queue CAS
  operations; budget's first-access seed in `get_budget`), just not for this
  guarantee. The underlying guarantee holds — arguably more robustly than
  the ADR describes, since the transaction also closes a
  debited-budget-with-no-reservation gap a single-key `try_save_state`
  wouldn't — but the named function is wrong.
- "Retried on 409/412" undersells `DaprIO._is_conflict`, which also
  recognizes a third conflict shape: a `500` carrying `ERR_STATE_SAVE`/"failed
  to set key" text, observed on the Redis state component.

**Confirmed as a real, currently-unmitigated risk (the ADR admits it; code
confirms no mitigation exists):**
- No `call_activity` in `payment/workflow.py` carries an explicit retry
  policy (grepped for `retry_policy`/`RetryPolicy` — zero hits anywhere in
  the codebase). The `try/except` around `pay_activity` does not wrap the
  compensating calls (`release_activity`, `fail_activity`) — a failure
  inside `release_activity` propagates uncaught out of the orchestrator,
  stranding a reservation.

**Questions:**
- "Why not two-phase commit, concretely?" — Facts: ADR-002 Context — no
  shared transaction coordinator across RabbitMQ + Redis + two
  independently-owned stores to enrol as a 2PC participant.
- "Walk me through exactly which function commits a budget reservation." —
  Facts: `commit_budget_and_reservation` → `DaprIO.try_execute_transaction`
  (not `try_save_state`).
- "What happens if `release_activity` itself fails?" — Facts: ADR-002's own
  admitted risk; no retry policy configured; the orchestrator's `try/except`
  doesn't cover the compensating calls.
- "Your concurrency test uses two requests — what about ten?" — Facts:
  `test_concurrent_reservations_never_overspend` and `verify.py`'s
  `guard_concurrent_budget` both use exactly two (`INV-1014A/B`).
  `BudgetLedger`'s retry loop is bounded at 8 attempts, then returns
  `ok=False, reason="budget contention"` rather than looping indefinitely.

---

## ADR-003 — Dapr as the integration layer

**Confirmed:**
- Exactly 2 application-code `.invoke()` call sites, grepped directly:
  `config_client.py` and `approval/app.py`.
- 8 pub/sub topics in `contracts.py`'s `Topics` class, matching the ADR's
  list by name exactly.
- `dapr/components/` currently holds only `pubsub.yaml`, `statestore.yaml`,
  `secretstore.yaml` — no configuration component, consistent with "removed."
- `secretKeyRef` in `pubsub.yaml`, `actorStateStore: true` and `keyPrefix` in
  `statestore.yaml`, and `placement`/`scheduler` in `docker-compose.yml` all
  confirmed present exactly as described.

**Stale/inaccurate:**
- Same `try_save_state` claim as ADR-002, repeated in the State section —
  see above.

**Questions:**
- "What's the actual cost if one `daprd` sidecar is slow?" — Facts: ADR-003
  Risks — named explicitly as being in the critical path for its service.
- "Why not Dapr's own configuration building block, given you lean on Dapr
  everywhere else?" — Facts: ADR-003 Decision — one mutation path beats two
  systems that could disagree; the unused `configuration.redis` component's
  removal is confirmed by its absence today.
- "Only two call sites for service invocation in the whole codebase —
  deliberate?" — Facts: confirmed by grep, exactly two.

---

## ADR-004 — Config fallback fails safe

**Confirmed — this document is the most precisely maintained of the four:**
- The embedded code quote (lines 19–31 of the ADR) matches
  `config_client.py` **character-for-character**.
- The line-number citation `config_client.py:20-31` is **exact**: line 20 is
  `async def fetch_thresholds(`, line 31 is `return AutonomyThresholds()`.
- The docstring line-range citation (3–5) is also exactly right.
- `config`'s own `get_thresholds` handler confirmed to have no fallback
  logic of its own — the fallback is entirely client-side in `decision`, as
  claimed.

**Stale/inaccurate:** none found.

**Questions:**
- "You say an outage can never widen the ceiling, but your own Risks section
  says it could run looser than intended — which is it?" — Facts: see
  Cross-document consistency flag #1, above.
- "Is the fallback actually alerted on, or just logged?" — Facts:
  `thresholds_fetch_failed` is a structlog warning line only. ADR-004's own
  Risks section says this "should be wired to monitoring, not just
  written" — i.e., today it isn't.
- "Has the two-copies-of-defaults drift risk you named ever actually
  happened?" — Facts: checked in this pass — `config/defaults.py`'s
  `DEFAULT_THRESHOLDS` and `decision/models.py`'s `AutonomyThresholds`
  defaults currently match exactly across all 9 fields (including the FX
  rate dict). Named as a standing risk, not a reported incident.

---

## docs/PRODUCT-DILEMMA.md

**Confirmed:**
- Both file paths cited (`router.py`, `services/config/approvalflow_config/defaults.py`)
  exist and match what's described.
- **Evidence table and sensitivity table verified by actual execution** —
  `python tools/dilemma_report.py` reproduces both tables **exactly**, row
  for row, including the `INV-1013` adversarial marker and all three
  sensitivity rows. Strongest confirmation available in this whole review:
  ground-truth execution, not code reading.

**Stale/inaccurate:** none found.

**Questions:**
- "If the ceiling isn't your primary safety control, why have one at all?"
  — Facts: the doc's own words — "one of several independent gates... not
  the primary safety control." Sharpest form of this question: for the
  `other` category specifically, `policy_engine.py` has no dedicated cap
  (`# OTHER: no specific cap... return True`) — the ceiling (+ confidence +
  the category-agnostic hard stops) is the *only* amount-based backstop for
  that category, with no secondary cap behind it the way `SAAS-01`/`HW-02`/
  `TRAVEL-02` back up meals/hardware/travel.
- "What stops someone from splitting one large expense into many small ones
  under the ceiling?" — Facts: confirmed — nothing in this codebase checks
  submission velocity or aggregate spend per submitter/vendor/department;
  `GLOBAL-DUP` only catches an *identical* resubmission (same vendor +
  invoiceNumber + total). Not addressed anywhere in the docs reviewed —
  genuinely open today.
- "The sensitivity table is 20 fixtures — how would you know it holds on
  real traffic?" — Facts: `dilemma_report.py` only ever replays the fixture
  set; `FUTURE-WORK.md` Idea 1 names "cold start... only 20 fixtures" as a
  present weakness.
- "Where do the category caps ($75/attendee, $200/mo SaaS, etc.) come from —
  did you choose those?" — Facts: specified in `Examples/policy.md` §§1–4;
  `PRODUCT-DILEMMA.md` is scoped specifically to §6 (the ceiling + confidence),
  per its own opening quote.

---

## docs/ARCHITECTURE.md

**Confirmed:**
- Sequence-diagram mechanics all verified exactly against current code: the
  `fp::`/`idem::` claim keys, the `decision::{trackingId}` state key, the
  `HumanDecision` event name (`HUMAN_DECISION_EVENT` constant), the
  `raise_workflow_event` function name, the approval POST path and its
  `approver`-role guard, and the config fallback behavior/log line name
  (`thresholds_fetch_failed`).
- Payment-flow diagram's activity names, function names (`BudgetLedger.reserve()`,
  `provider.execute_payment()`, `BudgetLedger.confirm()`, `BudgetLedger.release()`,
  `_mark_failed()`), and state key names (`payment::{trackingId}`) all
  confirmed exact.
- Requirements-mapping table's M11 row (durable HITL survives a restart) is
  more than an architectural claim — `tools/verify.py`'s
  `journey_escalate_and_resume` actually issues `docker compose restart`
  against `decision` and `decision-dapr` between the pause and the approve
  call. An executed proof, not just a diagram claim.

**Stale/inaccurate:**
- The closing note under the sequence diagram states *"reporting subscribes
  to every topic."* Confirmed false by direct count: `reporting/app.py`'s
  `_TOPICS` list has 6 entries; `contracts.py` defines 8. Not subscribed:
  `item.status-changed`, `payment.requested`. (The narrower subscription is
  arguably the more correct design — subscribing to all 8 would create
  redundant audit entries — but the sentence as written overclaims.)
- The N1 note (three examples: submit/queue/audit, "enforced by
  `require_role` in each owning service") doesn't literally claim blanket
  coverage, but doesn't surface the one real exception either: **all three
  of `config`'s endpoints carry no `require_role`/JWT check** — `GET
  /config/thresholds` and `GET /config/policy` are fully open, `PUT
  /config/thresholds` uses a separate `X-Config-Token` header instead.
  `config` *is* proxied by the gateway (`location /config`). Verified by
  grep across every `app.py` in the repo — `config/app.py` has zero
  `require_role` import or usage.

**Questions:**
- "Does reporting really subscribe to every topic?" — Facts: no, 6 of 8;
  see above.
- "Is N1 enforced everywhere the diagram implies?" — Facts: everywhere
  except all of `config`'s endpoints — a real, gateway-reachable exception
  the diagram doesn't mention.
- "Prove the HITL pause is actually durable, not just architecturally
  durable." — Facts: `journey_escalate_and_resume` restarts `decision` and
  `decision-dapr` between pause and resume, waits 6s for reconnect, then
  approves and asserts the item reaches `paid`.

---

## docs/FUTURE-WORK.md

**Confirmed:**
- `EscalationResolved` (`contracts.py`) carries both `action` and
  `usd_amount` as claimed.
- `PUT /config/thresholds` is real and runtime-effective (confirmed via
  `test_put_overrides_thresholds_without_redeploy` and direct code read).
- `tools/dilemma_report.py` does replay arbitrary threshold overrides
  without touching shipped code defaults — confirmed by running it.
- `agent/client.py`'s `build_chat_client` is exactly the described
  provider-selection seam.

**Stale/inaccurate:**
- *Precision nit:* Idea 2 states `DecisionAgent`... "is the single seam
  **the router** sees." Literally, `router.py` never receives a
  `DecisionAgent` — it only ever sees the resulting `AgentAssessment`. It's
  `decision_core.decide()` that depends on `DecisionAgent`. The practical
  point (swapping in a council changes nothing downstream of that seam)
  still holds; "the router" here is used loosely to mean "the decision
  pipeline," not the `router.py` module specifically.

**Questions:**
- "Idea 2 says a council 'strengthens calibration, not safety' — why is
  that true given the router's own boundaries?" — Facts: `route()` never
  reads `recommendation` and only uses `confidence` to downgrade; replacing
  one model's confidence with an agreement-based number changes what value
  feeds the same one-directional gate — it doesn't touch hard stops, the
  ceiling check, or `route()`'s logic at all.
- "If you had more time, how would you move past fixed threshold numbers?"
  — Facts: two sketched directions, both keeping the router as the only
  thing that ever touches money — history-driven proposals (fed by
  `escalation.resolved` + reporting's audit trail, replayed against the
  20-fixture floor, applied via the existing `PUT` endpoint) and an LLM
  council (agreement-as-confidence, still one `AgentAssessment` out).
- "What are the known weaknesses of the history-driven idea?" — Facts: named
  directly in the doc — cold start (only 20 fixtures exist), selection bias
  (auto-approved items never get a human label, so lowering a threshold is
  better-evidenced than raising one), gaming/drift (hard stops must stay
  outside the learned surface).

---

## Methodology note (for your own reference, not for the defense)

- Full test suite: 168 passed, 0 failed, scratch venv, current `main`.
- `tools/dilemma_report.py`: output diffed manually against
  `PRODUCT-DILEMMA.md`'s two tables — identical.
- Auth table: built from `grep -n "require_role\|@app\." services/*/*/app.py`
  (and `services/decision/approvalflow_decision/service/app.py` separately,
  nested one level deeper) — not from memory.
- Service-invocation call sites, topic count, and Dapr component inventory:
  all grepped directly, not recalled.
- Nothing in `services/`, `dapr/`, `gateway/`, `ui/`, or `docs/adr|PRODUCT-DILEMMA|ARCHITECTURE|FUTURE-WORK`
  was modified to produce this document.
