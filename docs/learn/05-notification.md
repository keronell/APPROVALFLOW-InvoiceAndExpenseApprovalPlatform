# Notification

Package: `services/notification/approvalflow_notification/` · App id:
`notification` · Port `8007` (loopback) · endpoints proxied at
`/api/notifications`.

## What it owns, and why it's a separate service

Notification owns **per-submitter inboxes** — `inbox::{submitter} →
list[Notification]` — plus a small lookup, `ref::{tracking_id} →
ItemRef(submitter, invoice_id)`, that exists only because the events telling
notification "this item just reached a final state" don't themselves carry
the submitter's email; only the original `item.submitted` event does.

It exists as its own service to satisfy M8 literally: *"deliver the final
result later via a notification channel."* That's a genuinely different
concern from intake's `GET /api/invoices/{id}` (F2 — "let me check on the
thing I have the tracking id for"). Notification is addressed by **submitter
identity**, not tracking id — you don't need to remember a tracking id to
find out what happened to your stuff — and it deliberately only reports
**terminal** outcomes, filtering out every intermediate status change, to
model what a real notification channel (email, push) should actually do:
tell you when something is *done*, not on every internal step.

## Inputs / outputs

**Endpoints** (`app.py`):

| Method & path | Role required | Purpose |
|---|---|---|
| `GET /dapr/subscribe` | — (Dapr-internal) | Declares the two subscriptions below |
| `POST /events/item-submitted` | — (Dapr-internal) | Records the ref (who to notify later) |
| `POST /events/status-changed` | — (Dapr-internal) | Delivers a notification if the status is terminal |
| `GET /api/notifications?submitter=...` | any authenticated | The inbox (M8) |

**Publishes:** nothing — notification is a pure sink. Worth noting
explicitly, since every other service in this system publishes at least one
event; this is the one that only ever consumes.

**Subscribes:** `item.submitted`, `item.status-changed`.

**State it owns:** `ref::{tracking_id} → ItemRef`,
`inbox::{submitter} → list[Notification]`.

## Non-obvious design choices

**1. `TERMINAL_STATUSES` is a small, explicit allowlist** (`service.py:20`):
`paid`, `payment_failed`, `rejected`, `sent_back`. Notably *absent*:
`auto_approved`/`approved` (intermediate — payment still has to run) and
`duplicate`. The code comment is direct about the second one: *"`duplicate`
never reaches here: intake short-circuits duplicates without publishing"* —
one more confirmation (alongside `01-intake.md` and the router's dead
`already_processed` branch) that a duplicate submission never enters the
event pipeline at all.

**2. Out-of-order delivery is handled explicitly, not assumed away.**
`item.submitted` and `item.status-changed` are two independently-delivered
pub/sub messages — nothing guarantees the ref-establishing event is
processed before a terminal status event for the same item arrives, even
though in this system's logic one always *logically* precedes the other.
`on_status_changed` (`service.py:104`) checks for the ref and, if it's
missing, raises `RefNotReadyError` rather than either silently dropping the
notification or crashing. The HTTP handler (`app.py:100-109`) turns that into
`{"status": "RETRY"}`, which tells Dapr to redeliver. This is a deliberate
trade: a notification might arrive a little late, but it is never silently
lost — a real M8 violation would be far worse than a delay
(`test_terminal_before_submitted_asks_for_retry`).

**3. The dedupe key is `(tracking_id, status)`, not just `tracking_id`, and
the dedupe check and the write share one atomic operation.**
`InboxStore.append` (`service.py:58`) checks the existing inbox for that
exact pair inside the same read used for the conditional write — there's no
separate "have I seen this" pre-claim. That's what makes a crash between the
check and the write safe: a failed append is simply retried by the pub/sub
redelivery (no stale "already seen" marker was left behind to wrongly
short-circuit it), and a redelivery *after* a successful append is a genuine
no-op (`test_failed_append_is_retried_by_redelivery`).

## If this crashes right now

- **Between reading the ref and `append()` persisting the notification:** the
  handler never returns `200`, so Dapr redelivers. Because the dedupe check
  and the write are against the same fresh read each time, redelivery either
  completes the still-missing write, or — if the write actually landed a
  moment before the crash — sees the existing entry and does nothing.
  Exactly one notification either way, never zero, never two.
- **`notification` is down entirely:** `item.submitted` and
  `item.status-changed` events queue durably in RabbitMQ
  (`durable: true`, `autoAck: false`) until it's back — nothing is dropped,
  notifications are just delayed. The submitter isn't blind in the meantime:
  `GET /api/invoices/{id}` on `intake` is a completely independent
  subscription and keeps working regardless of notification's health.
- **The ref was never written because notification was down for the entire
  window between `item.submitted` and the terminal event:** `RefNotReadyError`
  fires and Dapr keeps redelivering the terminal event. This self-resolves
  once notification comes back up and works through the backlog — RabbitMQ
  won't drop a durable, unacknowledged message — though I wouldn't claim a
  strict ordering guarantee between the two topics; the mechanism that
  actually guarantees eventual delivery here is "nothing ever permanently
  discards an unacked message," not "messages arrive in the order they were
  published."
