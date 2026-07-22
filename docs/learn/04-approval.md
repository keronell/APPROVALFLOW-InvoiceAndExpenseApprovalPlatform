# Approval

Package: `services/approval/approvalflow_approval/` · App id: `approval` ·
Port `8005` (loopback) · endpoints proxied at `/api/approvals`.

## What it owns, and why it's a separate service

Approval owns **the human work queue**: one `QueueItem` per escalated
tracking id (the escalation payload, its `pending`/`resolved` status, who
decided and what, and — this is the part worth dwelling on — its own
side-effect completion tracking), plus a small index for listing.

It's split out from decision for a UX/product reason first: F4/F5 want a
queue that shows an approver *only* what was escalated, with a one-click
approve/reject/send-back action — a human-facing surface that shouldn't be
tangled up with Dapr Workflow internals or money-routing logic. F6 ("no
rubber-stamping") is partly enforced by construction here, not just by
policy: `GET /api/approvals` only ever returns items decision already
decided it couldn't safely auto-handle — an approver literally cannot see,
and therefore cannot casually rubber-stamp, anything the router was willing
to clear on its own.

The deeper reason it's worth its own service: it owns a **genuinely different
consistency problem** than anything else in the system. A browser click is
one user action, but the HTTP request behind it can be retried, duplicated
by a flaky network, or racing another approver's click on the same item —
and it has to turn into *exactly one* workflow resume and *exactly one*
`escalation.resolved` publish, no matter how many times the POST lands. That
problem doesn't belong inside `decision` (which shouldn't have to know about
browser retry semantics) or anywhere else.

## Inputs / outputs

**Endpoints** (`app.py`):

| Method & path | Role required | Purpose |
|---|---|---|
| `GET /dapr/subscribe` | — (Dapr-internal) | Declares the subscription below |
| `POST /events/escalation-requested` | — (Dapr-internal) | Enqueues a new item |
| `GET /api/approvals` | `approver` | The queue (F4) |
| `POST /api/approvals/{tracking_id}/decision` | `approver` | The one-action decide (F5) |

**Publishes:** `escalation.resolved`.

**Also calls out via Dapr *service invocation*, not pub/sub:**
`POST api/decisions/{tracking_id}/human-decision` on `decision`
(`DaprDecisionGateway.raise_human_decision`, `app.py:48`) — deliberately a
targeted, direct call rather than a broadcast, because it needs to wake up
one specific paused workflow instance, not announce something to whoever's
listening.

**Subscribes:** `escalation.requested`.

**State it owns:** `queue::{tracking_id} → QueueItem`, plus `queue-index`, a
hand-maintained list of tracking ids. That index exists because Dapr's Redis
state store is a plain key/value store with no "list keys matching a prefix"
query — `list_items()` has nowhere else to get the set of tracking ids from.

## Non-obvious design choices

**1. This is the one multi-step, must-not-run-twice piece of the system that
is *not* built on Dapr Workflow.** Decision and payment get replay-safety
from `dapr.ext.workflow`; `decide()` (`app.py:118`) instead hand-rolls a
four-stage protocol directly in the endpoint, each stage its own atomic
ETag-CAS state operation with its own retry loop:

1. **Claim** — `try_resolve`: atomic `PENDING → RESOLVED`, stamping the
   decision. Exactly one concurrent caller wins.
2. **Lease** — `try_begin_side_effects`: atomic acquire of a side-effects
   lease (a timestamp field, CAS-guarded), so only the request holding it may
   run the next step.
3. **Side effects** — `raise_human_decision` (resumes the paused workflow)
   *and* `publish_resolved` (`escalation.resolved`), run only by the lease
   holder.
4. **Mark done** — `mark_side_effects_completed`: flips
   `side_effects_completed = True`, which is the only thing that lets a
   caller ever receive a `200` claiming success.

**2. The claim and its side effects are deliberately *not* one transaction —
they can't be; a network call to another service can't live inside a Dapr
state transaction.** `QueueItem.side_effects_completed` is effectively an
outbox flag: it stays `False` until *both* downstream effects have actually
succeeded. That means a request can legitimately win the claim (the item is
now `RESOLVED`) and then fail or crash before finishing the side effects —
and the retry path is built specifically to detect exactly that state and
replay *only* the missing side effects, reading the action to replay from
**the stored `QueueItem`**, never from the retry request's own body
(`app.py:162-167`, comment: *"Replay from the STORED decision (the claim
winner's), so a retry carrying a different action can't override the
recorded outcome"*). A retry that (accidentally or maliciously) POSTs a
different `action` cannot change what actually gets executed.

**3. The lease TTL (`SIDE_EFFECT_LEASE_TTL = 30s`, `store.py:21`) exists
purely for crash recovery, not normal operation.** A failed side effect
releases the lease immediately in the `except` block
(`release_side_effects_lease`, `app.py:182`), so a normal retry never waits
out the TTL — it only matters if the lease holder dies mid-flight without
reaching that `except`.

**4. A losing concurrent request doesn't fail fast, and doesn't return
success while the work is only leased — it waits.** `decide()` polls up to
15 times at 0.2s (`_SIDE_EFFECT_WAIT_ATTEMPTS`/`_SIDE_EFFECT_POLL_SECONDS`)
for the lease holder to finish, returns the completed item the moment
`side_effects_completed` is true, takes the lease over itself if it goes
stale or is released, and only gives up with a retryable `503` if the holder
is still working after the whole wait window. The practical guarantee: a
`200` from this endpoint always means the work is *actually done*, never
"claimed but who knows."

## If this crashes right now

- **Before `try_resolve` runs:** nothing happened yet; the item is still
  `pending`; a fresh POST retries cleanly — `try_resolve` is a single atomic
  operation with no partial state to clean up.
- **After `try_resolve` succeeds (item is `RESOLVED`) but before any side
  effect starts:** on the next POST, `item.status == PENDING` is false so the
  claim step is skipped, but the loop still runs: `side_effects_completed` is
  `False`, no lease is held, so this new request immediately acquires the
  lease and runs the side effects — using the *original* stored decision, not
  whatever the new request's body says.
- **Right after acquiring the side-effects lease, before finishing:** the
  item is stuck for **up to 30 seconds** — no other request can take the
  lease until it goes stale. After that window, a fresh request takes over
  and replays the side effects from the stored decision. Worst case is a
  30-second stall on that one item, not a lost or duplicated decision.
- **`raise_human_decision` succeeds but `publish_resolved` then fails (or
  vice versa) within one attempt:** the `try/except` wraps both calls
  together (`app.py:168-183`) — either failing releases the lease and
  surfaces a `5xx`, and the retry re-runs *both* calls again, including
  `raise_human_decision` a second time. The code's own reasoning for why
  that's safe (not something I verified against Dapr Workflow's internals
  myself, but the documented contract this code relies on): *"the workflow
  consumes the human-decision event once; reporting dedupes
  `escalation.resolved`, so replay is safe."*
- **`approval` is down while `escalation.requested` events arrive:** not
  lost — standard RabbitMQ redelivery. `on_escalation` (`app.py:88`) checks
  for an existing queue item before upserting, so even a redelivery of the
  same escalation arriving *after* a human already resolved it does not
  reopen or overwrite the resolution
  (`test_redelivered_escalation_does_not_reopen_resolved_item`).
