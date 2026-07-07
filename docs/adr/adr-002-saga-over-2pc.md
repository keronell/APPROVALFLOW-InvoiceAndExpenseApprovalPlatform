# ADR-002 — Payment saga with compensation vs. two-phase commit

- Status: context drafted — decision/consequences pending (author)
- Date: 2026-07-07

## Context

The payment flow must guarantee a **consistent outcome across services, with
compensation/rollback on failure — no orphaned reservations, no partial or
double payments** (M9), and idempotency for redelivered events and retried
payments (M10). The assignment explicitly allows any approach that provides
these guarantees but requires the choice to be justified.

The distributed facts that make this non-trivial:

- The money path spans **multiple state owners**: the payment service's budget
  (`budget::{department}`), reservation (`reservation::{id}`) and payment
  record (`payment::{id}`) in its own Redis-backed Dapr state store, plus
  submitter-visible status held by intake — updated only via RabbitMQ events.
  Services never touch each other's stores (per-app `keyPrefix`).
- The trigger is asynchronous (`payment.requested` over Dapr pub/sub); there is
  no shared transaction coordinator across RabbitMQ + Redis + two services, so
  a classic two-phase commit has nothing to coordinate with.

What the code does today (`services/payment/approvalflow_payment/`):

- `payment_workflow` (Dapr Workflow, `workflow.py`) runs the saga:
  `reserve_activity` → `pay_activity` → `confirm_activity` →
  `complete_activity`.
- **Compensation:** if `pay_activity` raises (`PaymentDeclined` — injected via
  the documented `test.failPayment` hook for Journey 4 / INV-1012),
  `release_activity` → `BudgetLedger.release()` restores the budget and marks
  the reservation `RELEASED`, then `fail_activity` publishes `payment.failed`
  + status `payment_failed`. A reserve refusal (insufficient budget) skips pay
  and needs no rollback — nothing was reserved.
- **No overspend under concurrency:** budget writes use ETag first-write
  concurrency (`DaprIO.try_save_state`, retried on 409/412), unit-tested with
  concurrent reservations and verified end-to-end with INV-1014A/B (two $600
  approvals against a $1,000 budget → exactly one paid, budget never below 0).
- **Idempotency:** the workflow instance id is the tracking id (a redelivered
  `payment.requested` schedules nothing), and every activity is individually
  idempotent (an existing reservation/payment record short-circuits).
- Verified end-to-end by `tools/verify.py` Journey 4: INV-1012 human-approved,
  payment forced to fail → status `payment_failed` and the department budget
  identical before/after (reserved then released — no leak).

## Decision

<!-- TODO: write this yourself — you defend this live -->

## Consequences

<!-- TODO: write this yourself — you defend this live -->
