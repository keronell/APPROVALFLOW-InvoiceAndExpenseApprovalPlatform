# ApprovalFlow — Learning Plan

Study order for going into an interview able to defend every part of this
system live, not just describe it. The order follows **the request path**
(gateway → intake → decision → approval/payment → the fan-out consumers), not
the alphabetical service list — you'll hit the deterministic core (the actual
point of the project) by stop 5, before you've even looked at payment or
notification.

**A naming note before you start:** you asked for explainer files
`01-intake.md … 07-config.md/06-reporting.md/07-gateway.md`, but the repo has
**eight** backend/gateway units, not seven — your example list skipped
`notification`. Rather than bury it inside `01-intake.md` (where it doesn't
really belong — it's its own deployable service with its own store), I gave it
its own file and shifted the two fixed-name files by one:

```
01-intake.md   02-decision.md   03-payment.md   04-approval.md
05-notification.md   06-config.md   07-reporting.md   08-gateway.md
09-cross-cutting.md   10-likely-questions.md
```

Say the word if you'd rather I fold notification into intake and keep your
original 08/09 numbering — easy to redo before I write more files.

Each stop below names the file(s) to read yourself, the explainer that covers
it, and 2–3 questions you should be able to answer cold before moving on. A
🟢/🔴 flag marks whether to skim or slow down.

---

## Stop 0 — Domain vocabulary (🟢 skim, ~15 min)

Everything downstream cites rule ids and fixture ids from these three files —
read them before any code, even though you already know the project.

**Read:** `Examples/policy.md`, `Examples/sample-invoices.json` (skim the
`fixtures` array and the `_about` field), `Documents/ASSIGNMENT.pdf` (skim).
**Explainer:** none — these *are* the source of truth.

**Self-check**
- What are the four independent conditions that must *all* hold for
  `auto_approve`? (policy.md §6)
- Name three hard stops that fire regardless of dollar amount.
- Why does the assignment explicitly forbid a $0 autonomy ceiling?

---

## Stop 1 — Entry point: the gateway (🟢 skim)

**Read:** `gateway/nginx.conf`, `gateway/proxy_params.conf`.
**Explainer:** `08-gateway.md`.

**Self-check**
- Which API prefix is deliberately **not** proxied by the gateway, and what
  would break (from a security standpoint) if it were?
- A request arrives with no `X-Correlation-Id` header. Where does the id that
  ends up in the response actually come from?
- What happens to the 61st request in one second from the same IP?

---

## Stop 2 — Shared kernel (🟡 moderate — read in full, it's small)

Every service is built on this. Skipping it makes every later file feel like
it's using magic.

**Read:** `services/shared/approvalflow_shared/contracts.py`,
`correlation.py`, `daprio.py`, `auth.py`, `web.py`, `logging.py`.
**Explainer:** `09-cross-cutting.md`.

**Self-check**
- Why is `tracking_id` always equal to `correlation_id` — where is that
  equality actually established?
- What does `_CamelModel`'s `extra="ignore"` buy the test suite and
  `tools/verify.py` specifically?
- `DaprIO.try_insert_state` vs `try_save_state` vs `try_execute_transaction` —
  what's the one-sentence difference between the three, and which one is the
  race-safe primitive behind duplicate detection?

---

## Stop 3 — Intake (🟡 moderate)

**Read:** `services/intake/approvalflow_intake/app.py`, `service.py`,
`store.py`, `dapr_adapters.py`; `tests/unit/test_intake_service.py`.
**Explainer:** `01-intake.md`.

**Self-check**
- Two different duplicate-defence mechanisms live in `IntakeService.submit()`
  — what's the difference between what the `Idempotency-Key` header protects
  against and what the fingerprint claim protects against?
- If the intake process dies between `store.put(record)` and
  `publisher.publish(...)`, what state is left behind — and does that violate
  any M9/M10 guarantee, or is it a narrower gap?
- Where does the *actual* GLOBAL-DUP decision get made — inside the
  `route()` function, or somewhere else entirely?

---

## Stop 4 — Decision, part 1: the deterministic core (🟡 moderate, foundational)

**Read:** `services/decision/approvalflow_decision/policy_engine.py`,
`models.py`; `tests/unit/test_policy_engine.py`.
**Explainer:** `02-decision.md` §1.

**Self-check**
- Why does an unsupported currency raise `GLOBAL-FX-UNKNOWN` instead of just
  converting at 1:1?
- `GLOBAL-FRAUD` requires an unknown vendor **and** a round number **and**
  (no line-item detail **or** off-hours). Using `INV-1008`, name a single-field
  change that would make the fraud check stop firing.
- Does `evaluate()` know the agent exists?

---

## Stop 5 — Decision, part 2: the router (🔴 slow down — but not because it's hard)

This is the single most important file in the repository (it's the entire
answer to M12/F10), and it is also one of the shortest and simplest — a pure
~100-line function with no I/O. Read it slowly not because the code is dense,
but because you need to be able to defend *why* it's structurally impossible
to bypass, line by line, live.

**Read:** `services/decision/approvalflow_decision/router.py` (all of it),
`tests/unit/test_router.py`, `tests/unit/test_fixtures_expected_routes.py`.
**Explainer:** `02-decision.md` §2.

**Self-check**
- Of all the fields on `AgentAssessment`, which *one* does `route()` ever
  read — and can that field ever *upgrade* a decision toward auto-approve?
- Walk `test_forced_approve_over_ceiling_still_escalates` from memory: the
  agent says confidence 1.0, recommend approve, on a $5,000 item. Explain in
  your own words why the router still escalates.
- `route()` accepts an `already_processed` flag that produces the `duplicate`
  route. Grep for it — is it ever set by the live, running system, or only by
  tests and `tools/dilemma_report.py`?

---

## Stop 6 — Decision, part 3: the agent (🟡 moderate)

**Read:** `services/decision/approvalflow_decision/agent/__init__.py`,
`assessor.py`, `client.py`, `tools.py`, `stub_client.py`, `settings.py`;
`tests/unit/test_agent_stub.py`.
**Explainer:** `02-decision.md` §3.

**Self-check**
- Trace what happens, end to end, if the LLM provider is completely
  unreachable when an invoice is being assessed. Does the workflow crash, hang,
  or something else?
- The stub client is explicitly *not* a mock. What does that distinction mean
  concretely, and why does CI rely on it instead of mocking `DecisionAgent`?
- `lookup_vendor` (an agent tool) and `invoice.vendor_known` (a submitted
  field) are two independent "is this vendor known" signals. Does anything in
  the deterministic path cross-check them against each other?

---

## Stop 7 — Decision, part 4: the Dapr Workflow (🔴 slow down — durable HITL)

**Read:** `services/decision/approvalflow_decision/service/workflow.py`,
`decision_core.py`, `app.py`, `config_client.py`; `tests/unit/test_decision_core.py`,
`test_config_client.py`; and `journey_escalate_and_resume` in `tools/verify.py`.
**Explainer:** `02-decision.md` §4.

**Self-check**
- While an item is waiting on a human, where does that "waiting" state
  physically live? Why does killing and restarting the `decision` container
  not lose it — what would you actually check in Redis to prove that?
- What is the Dapr Workflow **instance id** set to, and what specific bug does
  that choice prevent?
- If the `config` service is down when `decide_activity` runs, which
  thresholds get used — and could an outage ever *loosen* the posture?

---

## Stop 8 — Approval: the escalation queue (🔴 slow down — genuinely trickier than it looks)

Flagging this one explicitly: it is *not* built on Dapr Workflow, and the
hand-rolled protocol that replaces it is, in my read, the single trickiest
piece of concurrency reasoning in the codebase — more moving parts than the
payment saga, because payment gets replay-safety for free from the Workflow
engine and approval has to earn it in application code.

**Read:** `services/approval/approvalflow_approval/app.py` (the `decide()`
endpoint, closely), `store.py`; `tests/integration/test_approval_api.py`
(especially the four tests from `test_side_effects_replayed_after_gateway_failure`
down to `test_concurrent_claims_resolve_exactly_once`).
**Explainer:** `04-approval.md`.

**Self-check**
- Name the four stages `decide()` moves an item through and what each one
  guards against. Why isn't "claim + side effects" a single atomic step?
- Two approvers click Approve on the same item in the same instant. Trace both
  requests through the code — what does the loser actually receive back, and
  when?
- Approval crashes immediately after acquiring the side-effects lease but
  before finishing. How long is that item stuck, and what un-sticks it?

---

## Stop 9 — Payment: the saga (🔴 slow down — matches your instinct)

**Read:** `services/payment/approvalflow_payment/workflow.py`, `budget.py`,
`budget_dapr.py`, `provider.py`; `tests/unit/test_budget_ledger.py`,
`test_payment_provider.py`.
**Explainer:** `03-payment.md`.

**Self-check**
- Recite the forward path and the compensation path from memory:
  `reserve_activity → ? → ?`, and what runs if `pay_activity` raises?
- `commit_budget_and_reservation` writes the budget debit and the reservation
  record as **one** Dapr state transaction. What specific failure becomes
  possible if you split that into two separate writes?
- `INV-1014A` and `INV-1014B` both request $600 against a $1,000 budget,
  submitted at the same instant. Trace exactly why only one can win, down to
  the ETag.

---

## Stop 10 — The fan-out consumers: notification, config, reporting (🟢 lighter)

These three all subscribe to pipeline events but exist for different reasons —
that contrast is the point of this stop.

**Read:** `services/notification/approvalflow_notification/app.py`,
`service.py`; `services/config/approvalflow_config/app.py`, `defaults.py`,
`models.py`; `services/reporting/approvalflow_reporting/app.py`, `service.py`.
**Explainer:** `05-notification.md`, `06-config.md`, `07-reporting.md`.

**Self-check**
- Notification and reporting both subscribe to overlapping topics. What is
  each one actually *for*, such that neither is redundant with the other?
- `config`'s `PUT /config/thresholds` uses a bespoke `X-Config-Token` header
  instead of the JWT roles every other write endpoint uses. Deliberate choice
  or gap? What happens if `CONFIG_API_TOKEN` is unset in the environment?
- A `payment.completed` event gets redelivered by RabbitMQ (at-least-once
  delivery). Which of these three services could double-count something if it
  got this wrong, and what specific mechanism stops it?

---

## Stop 11 — UI + running the system live (🟢 do this, don't just read)

**Read (skim):** `ui/src/App.jsx`, `ui/src/views/*.jsx`.
**Do:** `cp .env.example .env && docker compose up`, then
`python tools/verify.py --no-up` against the running stack, then submit
`INV-1003` through the UI yourself and work it through the Approvals tab.
**Explainer:** `08-gateway.md` (UI section).

**Self-check**
- Submit the same fixture twice from the UI's "Edit as JSON" mode. What do you
  see in the Track tab the second time?
- Sign in as `submitter` and try to open the Approvals tab. What HTTP status
  is happening under the hood?
- In the Track tab, what's the difference between what a `submitter` sees and
  what an `admin` sees for the same tracking id?

---

## Stop 12 — Why it's built this way: ADRs + the dilemma (🟡 moderate)

By now you've seen *what* the code does everywhere it matters. This stop is
*why*, in the author's own words — read it last so it lands against real
context instead of abstractly.

**Read:** `docs/adr/adr-001-policy-engine-router-split.md`,
`adr-002-saga-over-2pc.md`, `adr-003-dapr-as-integration-layer.md`,
`adr-004-config-fallback-fail-safe.md`, `docs/PRODUCT-DILEMMA.md`,
`docs/FUTURE-WORK.md`.
**Explainer:** `10-likely-questions.md` turns these into rehearsed Q&A.

---

## Correcting your guess

You flagged the payment saga, the Dapr Workflow HITL pause/resume, and the
confidence/router logic as the tricky parts. Having now read all of it:

- **Payment saga — confirmed tricky.** Real complexity: compensation, ETag
  concurrency, and an idempotency story that spans a workflow engine, a
  ledger, and a fake payment provider.
- **Durable HITL pause/resume — confirmed tricky**, but the trickiness isn't
  in the concept ("wait for an event") — it's that trusting it requires
  understanding Dapr Workflow's durable-execution/replay model, which is
  unfamiliar territory if you haven't used an orchestration engine before.
  `tools/verify.py`'s `journey_escalate_and_resume` is the sharpest possible
  proof: it actually kills and restarts the `decision` container mid-flight.
- **The router / confidence logic — I'd push back on "tricky."** `router.py`
  is a ~100-line pure function, fully covered by tests, with no I/O. It's the
  most *important* file in the system, and the one to be able to recite from
  memory, but mechanically it's the simplest thing you'll read all week.

Two things I'd add to your list that are subtler than anything above:

1. **The approval service's claim → lease → side-effects → mark-done
   protocol** (Stop 8). It hand-rolls, in plain application code, guarantees
   that payment and decision get for free from Dapr Workflow. I'd rank it
   trickier than the saga precisely because there's no framework doing the
   heavy lifting — the crash-recovery, the lease TTL, and "replay from the
   *stored* decision, never from the retry's request body" are all bespoke.
2. **Idempotency is solved four different, unrelated ways** across the
   codebase — intake's atomic claim, the Workflow engine's instance-id dedup,
   payment's "durable pending marker before the side effect" pattern, and
   notification/reporting's "dedupe key committed in the same document as the
   effect" pattern. No two of them share code. Being able to name and compare
   all four cold is exactly the kind of question a sharp interviewer asks
   after you say "idempotency" once — that comparison is the spine of
   `09-cross-cutting.md`.

---

## Full file map (for reference as files land)

| # | File | Status |
|---|---|---|
| 00 | `00-LEARNING-PLAN.md` | this file |
| 01 | `01-intake.md` | next |
| 02 | `02-decision.md` | pending |
| 03 | `03-payment.md` | pending |
| 04 | `04-approval.md` | pending |
| 05 | `05-notification.md` | pending |
| 06 | `06-config.md` | pending |
| 07 | `07-reporting.md` | pending |
| 08 | `08-gateway.md` | pending |
| 09 | `09-cross-cutting.md` | pending |
| 10 | `10-likely-questions.md` | pending |
| 11 | `11-flow-analogies.md` | all route outcomes, walked with analogies + fixtures |
| 12 | `12-what-if-a-service-is-down.md` | two full flows traced through a mid-request outage |
| 13 | `13-flow-cheatsheet.md` | compact end-to-end reference: submission → decision → payment/approval → final status |
