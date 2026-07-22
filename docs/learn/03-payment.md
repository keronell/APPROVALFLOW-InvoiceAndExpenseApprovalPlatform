# Payment

Package: `services/payment/approvalflow_payment/` · App id: `payment` ·
Port `8004` (loopback) · endpoints proxied at `/api/budgets`, `/api/payments`.

## What it owns, and why it's a separate service

Payment owns **the department budgets, every reservation, and every payment
record** — the actual ledger. Three key namespaces, all in its own
Redis-backed state store: `budget::{department}`, `reservation::{tracking_id}`,
`payment::{tracking_id}`.

It's a separate service for a clean bounded-context reason: "should this be
approved" (decision) and "who approves it" (approval) are policy questions;
"move the money and never overspend or double-spend" is a ledger question,
with its own correctness discipline (M9/M10) that has nothing to do with how
the approval happened. That separation shows up concretely in the wiring:
**both** the auto-approve path and the human-approve path publish the exact
same `payment.requested` event, and payment doesn't know or care which one
fired it — decision and approval are choreographed into payment through pub/sub,
not orchestrated by it. That's what lets payment stay ignorant of *why* an
item is being paid.

It's also the natural home for an anti-corruption layer: `provider.py` is a
fake, swappable stand-in for a real payment gateway (Stripe, ACH, whatever) —
isolating that integration behind one narrow interface (`execute_payment`)
means the real integration, when it exists, only touches this one file.

## Inputs / outputs

**Endpoints** (`app.py`):

| Method & path | Role required | Purpose |
|---|---|---|
| `GET /dapr/subscribe` | — (Dapr-internal) | Declares the subscription below |
| `POST /events/payment-requested` | — (Dapr-internal) | Schedules `payment_workflow` |
| `GET /api/budgets/{department}` | `approver` | Current available budget |
| `GET /api/payments/{tracking_id}` | any authenticated | Payment status for one item |

**Publishes:** `payment.completed`, `payment.failed`, and `item.status-changed`
(`paid` or `payment_failed`) — always both a payment-specific topic and the
generic status topic, since `intake` only listens to the latter.

**Subscribes:** `payment.requested`.

**State it owns:** `budget::{department}` (an `available` amount + Dapr ETag),
`reservation::{tracking_id}` (amount, department, status:
`reserved`/`confirmed`/`released`), `payment::{tracking_id}` (status:
`attempting`/`paid`/`failed`, the payment id once paid).

## Non-obvious design choices

**1. The budget debit and the reservation record commit as ONE Dapr state
transaction, never two separate writes.** `DaprBudgetStore.commit_budget_and_reservation`
(`budget_dapr.py:28`) sends a single `/state/{store}/transaction` call with
both an `upsert` on `budget::{department}` (ETag-conditional) and an `upsert`
on `reservation::{tracking_id}`. `test_commit_is_atomic_on_conflict` proves
the failure mode this closes: on an ETag conflict, **neither** write lands —
there's no way to end up with a debited budget and no matching reservation
(which would silently leak money with nothing pointing back at it), nor a
reservation with no matching debit (a double-spend waiting to happen on
retry).

**2. No lock, no 2PC — just ETag first-write concurrency with a bounded
retry loop.** `BudgetLedger.reserve` (`budget.py:74`) re-reads
`get_budget()` fresh on every one of its 8 attempts, so it always checks
against current data, not stale data from the first read. That's the entire
no-overspend mechanism for `INV-1014A`/`INV-1014B` racing the same $1,000
budget: whichever request's conditional write lands first wins; the loser's
write is rejected (409/412, or the Redis-Lua-script 500 variant `DaprIO._is_conflict`
also recognizes), it re-reads the now-lower `available`, and on the next
attempt sees `amount > available` and returns `ok=False, reason="insufficient
budget"` — never a negative balance.

**3. Payment idempotency is a "durable marker written *before* the external
call" pattern — different from every other idempotency mechanism in this
codebase.** `_pay` (`workflow.py:90`) writes `{"status": "attempting",
idempotencyKey, amount}` to `payment::{tracking_id}` **before** calling
`execute_payment`. If the process crashes after the provider call succeeds
but before the `"status": "paid"` write lands, a retry calls
`execute_payment` again — with the *same* `idempotency_key`
(`request.tracking_id`). The fake provider derives the payment id
deterministically from that key (`hashlib.sha256(idempotency_key...)`), so
the retry gets back the identical payment id rather than a second charge —
modeling how a real gateway deduplicates on an idempotency key server-side.
Compare this to intake's *claim-before-you-touch-anything* pattern or
reporting's *dedupe-key-in-the-same-document* pattern — three genuinely
different shapes solving the same M10 problem (full comparison in
`09-cross-cutting.md`).

**4. Compensation is asymmetric on purpose, matching exactly which forward
step had an effect.** If `reserve_activity` returns `ok=False` (insufficient
budget), the workflow goes straight to `fail_activity` — no
`release_activity` call, because nothing was ever reserved
(`workflow.py:42-46`). Only a failure in `pay_activity` — *after* a
reservation genuinely exists — triggers `release_activity` first. Read the
`try/except` in `payment_workflow` literally: it wraps only the
`pay_activity` call, not the compensating steps after it.

## If this crashes right now

- **Between `reserve_activity` succeeding and `pay_activity` running:** on
  restart, Workflow replay resumes at `pay_activity` — `reserve_activity`'s
  result is already recorded, so it does not re-run. Belt-and-suspenders: even
  if something did call `reserve` again for the same tracking id,
  `BudgetLedger.reserve` checks for an existing `RESERVED`/`CONFIRMED`
  reservation first and short-circuits to `ok=True` without touching the
  budget a second time (`test_reserve_is_idempotent`).
- **Between the provider call succeeding and `pay_activity`'s state write
  landing:** exactly the case the "attempting" marker exists for — a retry
  (workflow replay or redelivery) re-calls the provider with the same key and
  gets the same payment id back, never a second charge.
- **`pay_activity` raises `PaymentDeclined`** (the `test.failPayment` hook, or
  a real decline): `release_activity` restores the budget and flips the
  reservation to `RELEASED`, then `fail_activity` records `status: failed` and
  publishes `payment.failed` + `item.status-changed(payment_failed)`. This is
  the exact scenario `tools/verify.py`'s `journey_payment_failure_compensation`
  checks end-to-end — it reads the department budget before submitting
  `INV-1012`, forces the payment to fail, and asserts the budget afterward is
  byte-identical (reserved, then fully released — no leak).
- **`payment` is down when `payment.requested` is published:** not lost —
  same RabbitMQ `requeueInFailure`/`autoAck: false` guarantee as every other
  subscriber in this system. If payment had already scheduled the workflow
  before dying, `get_workflow_state` makes the redelivery a no-op.
- **The honest gap, worth stating plainly if asked: compensation itself can
  fail, and nothing here automatically retries it.** No `call_activity` in
  `payment_workflow.py` passes an explicit retry policy (verified — there is
  no `retry_policy`/`RetryPolicy` anywhere in the codebase), and the
  `try/except` around `pay_activity` does **not** wrap `release_activity` or
  `fail_activity`. If `release_activity` itself raises — say
  `BudgetLedger.release` exhausts its own 8 internal attempts against
  persistent ETag contention and raises `RuntimeError` — that exception
  propagates out of the orchestrator uncaught, and the workflow instance ends
  in a failed state with the reservation still `RESERVED` and the budget still
  debited: a stranded reservation. This is not a hidden bug I'm the first to
  notice — it's ADR-002's own stated risk ("Compensation itself can fail,
  which would strand a reservation until retried... a poisoned or
  permanently-failing compensation would need operator attention rather than
  resolving itself"), and a good, honest answer if an interviewer pushes on
  "what could still go wrong here."
