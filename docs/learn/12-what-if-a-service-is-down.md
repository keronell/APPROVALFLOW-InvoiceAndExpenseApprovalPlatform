# What if a service is down mid-request?

A companion to the "If it crashes right now" section at the bottom of each
`0X-*.md` file — those are per-service and exhaustive. This file does the
opposite: it picks **two full requests**, walks them start to finish, and
asks "what if *this one* service happens to be down right when it matters?"
The point isn't new information — every fact below is already stated
somewhere in `01`–`08` — it's tracing it end to end so the shape of the
answer ("nothing is lost, it's just delayed" vs. "this genuinely can't
proceed") becomes obvious in context, not just as an isolated bullet.

**The one mechanism that explains almost every "nothing is lost" answer,
stated once so it isn't repeated eight times below:** every pub/sub topic in
this system rides on RabbitMQ configured `autoAck: false`,
`requeueInFailure: true`, `durable: true` (`dapr/components/pubsub.yaml`). A
subscriber that isn't running yet simply hasn't acknowledged anything — the
message sits durably in the queue and gets redelivered the moment that
service comes back up. Combine that with **workflow instance id = tracking
id** (decision, payment) or an explicit **idempotent upsert-if-absent check**
(every non-workflow subscriber), and a redelivered event after a restart is
provably a no-op rather than a duplicate action. Keep that mechanism in
mind — almost every scenario below reduces to "it's in the queue, waiting."

---

## Flow A — `INV-1012`: a normal capital-hardware approval, but **`payment` is down**

$9,500 hardware purchase from RackSpace Supplies. Over the $1,000 `HW-02`
capital threshold, so it always needs a human, regardless of confidence.

**Analogy:** think of this as a relay race. Intake is runner 1, decision is
runner 2, payment is runner 3. Runner 3 has twisted an ankle and isn't at
the exchange zone. That doesn't erase the baton, and it doesn't mean
runners 1 and 2 stop running — it means the baton sits at the exchange zone,
waiting, for as long as it takes runner 3 to get back on their feet. No one
re-runs legs 1 or 2 to compensate.

**Walking it, stage by stage, with `payment` down the whole time:**

1. **Intake** accepts the submission normally — payment being down has
   nothing to do with intake's job. `202` returned, `item.submitted`
   published. Unaffected.
2. **Decision** consumes it, runs the policy engine (`HW-02` hard stop:
   capital hardware, always human) and the agent, and the router produces
   `HUMAN_REVIEW`. Decision publishes `decision.made`,
   `item.status-changed(escalated)`, and `escalation.requested`. None of
   this touches payment. Unaffected.
3. **Approval** picks up `escalation.requested`, the item appears in the
   approver's queue. A human clicks Approve.
   `decide()` runs its claim → lease → side-effects protocol:
   `raise_human_decision` (service invocation to `decision`, resumes the
   paused workflow) and `publish_resolved` (`escalation.resolved`). **Still
   no dependency on payment.** Unaffected.
4. **Decision's workflow resumes**, `apply_human_decision_activity` runs,
   and the workflow publishes `payment.requested`. *This* is the first
   moment payment being down matters — but even here, "matters" just means
   the message goes into the queue and sits there. Decision's own workflow
   finishes and returns normally; publishing an event is fire-and-forget
   from decision's point of view, it doesn't block waiting for a consumer.
5. **`payment.requested` sits durably in RabbitMQ, unacknowledged**, because
   the one subscriber (`payment`) isn't running to ack it. Nothing is lost;
   nothing retries into a duplicate — it just waits.
6. **The submitter checks status in the meantime:** `GET
   /api/invoices/{id}` on intake still works (intake doesn't call payment
   directly) and reports whatever the last `item.status-changed` said —
   `escalated` → `approved`, but *not yet* `paid`, because `payment` never
   got to run and flip that status.
7. **`payment` comes back up.** Its Dapr sidecar redelivers the queued
   `payment.requested` event. `on_payment_requested` checks
   `get_workflow_state(tracking_id)` first — since this is genuinely the
   first delivery attempt payment has ever seen (it was down before, not
   crashed mid-processing), no existing workflow is found, so
   `payment_workflow` is scheduled fresh, exactly once. `reserve_activity`
   reserves against the `hardware` department's budget, `pay_activity` runs
   the fake provider, and `payment.completed` + `item.status-changed(paid)`
   fire. **Notification**, which has been queuing its own copy of
   `item.status-changed` this whole time for the same reason, delivers the
   "your item was paid" notice the moment it processes that now-current
   event.

**The honest edge case worth stating precisely:** if `payment` had been
*mid-workflow* when it crashed (rather than simply never having started),
the `get_workflow_state` check would instead find the existing instance and
the Workflow engine would replay its history — already-completed activities
(like a `reserve_activity` that already succeeded) are not re-run, only the
step after the crash resumes. The distinction that matters: "down the whole
time, comes back, sees a fresh event" and "crashes mid-flight, comes back,
resumes a replay" are two different code paths, but both land on the same
outcome — exactly one reservation, exactly one payment, nothing skipped,
nothing doubled.

**What genuinely does *not* happen:** decision does not retry publishing
`payment.requested` itself, does not poll payment's health, and does not
route the item back to a human a second time because payment is slow. The
approval is final and durable the moment step 3 completes — payment being
down is purely a downstream delay in *executing* an already-made decision,
never a reason to redo the decision.

---

## Flow B — `INV-1003`: an escalated item, but **`decision` is killed and restarted** while the item is paused waiting on a human

$1,820 weekend meal with no client name — `MEAL-02` (missing client
justification) plus over the ceiling, so it lands in `human_review`. This is
deliberately the *hardest* version of "a service is down," because unlike
Flow A (a message merely waiting in a queue), here a workflow is sitting in
a **suspended, waiting-for-a-human** state at the exact moment its own
service disappears.

**Analogy:** this is a hotel wake-up call system, not a live phone call. You
tell the front desk "wake me at 7am," and the note goes into the hotel's own
logbook (Redis), not into any one desk clerk's memory. It doesn't matter if
the clerk who took your request goes home, gets replaced, or if the whole
front desk closes and reopens with different staff by morning — the
logbook is what actually wakes you up, not the specific person who wrote it
down. And here's the part that make this differ from just "a message
waiting in a mailbox": the hotel isn't just holding an unopened envelope for
you (that's Flow A) — it's actively *remembering that it already asked the
question and is mid-conversation*, and that memory itself has to survive
the front desk closing.

**Walking it, stage by stage:**

1. **Intake and decision's first pass run normally** — policy engine +
   agent + router all agree: `HUMAN_REVIEW`. Decision's workflow reaches
   `yield ctx.wait_for_external_event(HUMAN_DECISION_EVENT)` and
   **suspends**. This is the critical fact: suspension isn't a Python
   coroutine idling in the `decision` process's RAM. Dapr Workflow is built
   on Dapr Actors, and the actor runtime persists the suspended
   orchestrator's history — including the fact that it's waiting on this
   specific event — into the same Redis `statestore` every other service
   uses (`actorStateStore: true` in `dapr/components/statestore.yaml`).
   `escalation.requested` is published; the item shows up in the approval
   queue. So far, identical to any other escalation.
2. **Now `decision` (and its Dapr sidecar) are killed.** Not gracefully
   stopped — killed, the way `tools/verify.py`'s
   `journey_escalate_and_resume` actually does it via `docker compose
   restart` between the pause and the approve call, to prove exactly this.
3. **While `decision` is down, nothing about this item is lost or even at
   risk**, because nothing about it lived in that process. The queue item
   sits in `approval`'s own store, entirely independent of decision's
   health. A human can look at the approvals queue, read the escalation
   reason, and click Approve **at any point during this outage** — the
   approver's UI talks to `approval`, not to `decision`, and doesn't even
   know decision is down.
4. **Suppose the human clicks Approve while `decision` is still down.**
   `approval`'s `decide()` runs its claim (atomic `PENDING → RESOLVED`,
   entirely local to `approval`'s own state) — that succeeds regardless of
   decision's health. It then tries the side effect:
   `raise_human_decision`, a Dapr **service invocation** call to `decision`.
   This call fails (no service to invoke). `approval`'s code wraps both
   side effects in one `try/except`; the failure releases the side-effects
   lease and surfaces a `5xx` to the caller — but **the claim itself is not
   rolled back**. The item is `RESOLVED` in approval's own store, with
   `side_effects_completed` still `False`. Nothing about "the human decided"
   is lost; only "telling decision about it" hasn't happened yet.
5. **`decision` restarts.** Its Workflow runtime reconnects to Redis and
   reconstructs every suspended instance's state by replaying history up to
   the `wait_for_external_event` point — this is what "durable" actually
   buys: the instance the human's decision needs to reach still exists,
   found by the same key it always had (the tracking id), even though the
   process that originally created it is gone and a fresh one has taken its
   place.
6. **A retry of the approval decision** (the approver clicking again, or a
   client-side retry) hits `decide()` again. The claim step is now a no-op
   (`item.status == PENDING` is already false), but the loop still runs and
   sees `side_effects_completed = False` with no lease held — it
   immediately re-acquires the lease and replays *only* the side effects,
   reading the action to replay **from the stored `QueueItem`**, never from
   the new request's body. `raise_human_decision` now succeeds (decision is
   back), reaching the actual suspended instance and calling
   `client.raise_workflow_event(tracking_id, HUMAN_DECISION_EVENT, ...)`,
   which finds the durably-waiting instance and resumes it exactly at the
   `yield` point. `apply_human_decision_activity` runs, `payment.requested`
   publishes, and the whole thing proceeds exactly as if decision had never
   gone down at all.

**The one-sentence version, worth having ready cold:** an item paused on a
human is the case this whole architecture is *optimized* for, not a weak
point — the approver can act hours or days later, against a `decision`
instance that may not even be the same container that paused it (a restart,
a redeploy, a version upgrade), and the outcome is identical either way,
because the state that actually matters was never in any one process's
memory to begin with.

**Contrast this deliberately with Flow A:** Flow A's "service down" story is
the *simple* shape — an event sits unacknowledged in a queue, waiting for a
consumer to exist. Flow B's is the *interesting* shape — a stateful,
multi-step conversation (decision ⇄ human) has to survive the process that
started it disappearing entirely, and the only reason that works is that
the suspended state was written to Redis, not kept in RAM. If asked "which
failure mode actually tests your architecture," Flow B is the honest
answer — Flow A is just "a durable queue did its job."

---

## What these two flows deliberately don't cover

Both flows above pick services whose outages have a clean, fully-recoverable
story. Two honest gaps exist elsewhere in the system, already named
precisely in their own service files — worth knowing they exist so you
don't imply every outage is this clean:

- **`intake` itself going down between persisting a record and publishing
  `item.submitted`** loses that one submission permanently (no outbox
  pattern, N3 not attempted) — see `01-intake.md`. This is a narrower gap
  than either flow above: no money ever moved, but the submitter does have
  to resubmit, with no orphaned record they can even query.
- **Payment's compensation step itself failing** (`release_activity`
  raising after `pay_activity` already failed) has no automatic retry
  anywhere in the codebase — see `03-payment.md`'s closing section. Unlike
  Flow A and B, this one *is* a stranded state that would need operator
  attention, and ADR-002 says so explicitly rather than papering over it.
