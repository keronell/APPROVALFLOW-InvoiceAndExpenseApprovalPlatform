# ADR-002 - Payment saga with compensation vs. two-phase commit

- Status: accepted
- Date: 2026-07-07

## Context

The payment flow must guarantee a **consistent outcome across services, with
compensation/rollback on failure - no orphaned reservations, no partial or
double payments** (M9), and idempotency for redelivered events and retried
payments (M10). The assignment explicitly allows any approach that provides
these guarantees but requires the choice to be justified.

The distributed facts that make this non-trivial:

- The money path spans **multiple state owners**: the payment service's budget
  (`budget::{department}`), reservation (`reservation::{id}`) and payment
  record (`payment::{id}`) in its own Redis-backed Dapr state store, plus
  submitter-visible status held by intake - updated only via RabbitMQ events.
  Services never touch each other's stores (per-app `keyPrefix`).
- The trigger is asynchronous (`payment.requested` over Dapr pub/sub); there is
  no shared transaction coordinator across RabbitMQ + Redis + two services, so
  a classic two-phase commit has nothing to coordinate with.

What the code does today (`services/payment/approvalflow_payment/`):

- `payment_workflow` (Dapr Workflow, `workflow.py`) runs the saga:
  `reserve_activity` → `pay_activity` → `confirm_activity` →
  `complete_activity`.
- **Compensation:** if `pay_activity` raises (`PaymentDeclined` - injected via
  the documented `test.failPayment` hook for Journey 4 / INV-1012),
  `release_activity` → `BudgetLedger.release()` restores the budget and marks
  the reservation `RELEASED`, then `fail_activity` publishes `payment.failed`
  + status `payment_failed`. A reserve refusal (insufficient budget) skips pay
  and needs no rollback - nothing was reserved.
- **No overspend under concurrency:** budget writes use ETag first-write
  concurrency (`DaprIO.try_save_state`, retried on 409/412), unit-tested with
  concurrent reservations and verified end-to-end with INV-1014A/B (two $600
  approvals against a $1,000 budget → exactly one paid, budget never below 0).
- **Idempotency:** the workflow instance id is the tracking id (a redelivered
  `payment.requested` schedules nothing), and every activity is individually
  idempotent (an existing reservation/payment record short-circuits).
- Verified end-to-end by `tools/verify.py` Journey 4: INV-1012 human-approved,
  payment forced to fail → status `payment_failed` and the department budget
  identical before/after (reserved then released - no leak).

## Decision

I implement the payment flow as an orchestrated saga with compensation, not
a two-phase commit:

1. A Dapr Workflow (`payment_workflow`) orchestrates the steps as ordered
   activities: `reserve_activity` → `pay_activity` → `confirm_activity` →
   `complete_activity`.
2. Failure is handled by compensation, not rollback: a `PaymentDeclined`
   from `pay_activity` triggers `release_activity` (restores budget, marks the
   reservation `RELEASED`) then `fail_activity` (publishes `payment.failed` +
   status `payment_failed`). A reserve refusal needs no compensation - nothing
   was reserved.
3. Concurrency safety rests on ETag first-write wins
   (`DaprIO.try_save_state`, retried on 409/412) on the budget key, so two
   approvals racing the same budget can never overspend it.
4. Idempotency is structural: the workflow instance id *is* the tracking id
   (a redelivered `payment.requested` schedules nothing), and every activity
   short-circuits on an existing reservation/payment record.

I do not use two-phase commit because there is no resource manager to enrol:
the money path spans RabbitMQ, Redis, and two independently-stored services with
no shared transaction coordinator, so 2PC has nothing to coordinate and its
blocking prepare phase would couple availability across all of them.

## Consequences

Positive. The saga gives me the M9/M10 guarantees without a distributed
transaction coordinator: compensation restores the budget on failure (verified
by tools/verify.py Journey 4 - INV-1012's budget is identical before and
after a forced payment failure, so no reservation leaks), ETag first-write wins
prevents overspend under concurrency, and idempotency keyed on the tracking id makes redelivered events and retried
payments safe by construction. Each service keeps its own store, so the design
stays consistent with the per-service-state boundary the rest of the system
uses.

Trade-offs. Consistency is eventual, not atomic: between reserve and
pay there is a real window where budget is held but no payment exists, and an
observer can see that intermediate state - I accept it because compensation
closes the window and the alternative (2PC) would trade that visibility for
blocking locks across services. I also pay in complexity: every activity must
be written to be individually idempotent and every forward step must have a
matching compensating step, which is more code and more test surface than a
single transaction would need.

Risks and follow-ups. Compensation is only as good as its coverage - a
forward step added later without a matching release path could leak a
reservation, so the saga's safety depends on that discipline being maintained
. Compensation itself can fail, which would strand a reservation until retried; the
workflow's idempotent activities make a retry safe, but a poisoned or
permanently-failing compensation would need operator attention rather than
resolving itself. Finally, the ETag retry loop assumes contention stays low
enough that first-write-wins converges; pathological contention on a single
department budget would surface as repeated 409/412 retries rather than
incorrect state - safe, but a latency signal worth watching.
