# Reporting

Package: `services/reporting/approvalflow_reporting/` · App id: `reporting`
· Port `8006` (loopback) · endpoints proxied at `/api/reports`, `/api/audit`.

## What it owns, and why it's a separate service

Reporting owns two things, both cross-cutting by nature: aggregate
**`Metrics`** (submitted/auto_approved/escalated counts, USD sums, the
auto-approval rate) for F8's dashboard, and a full **per-tracking-id audit
trail** — every tracked event, in arrival order — for F9.

It's a separate service because "look across everything that happened" is a
fundamentally different shape of concern from any single service's own
operational state, and no other service should have to accumulate a
cross-cutting history just to do its own job (that would violate the
"services own their state, nothing reads another's store" rule everywhere
else). It's also the one place `correlation_id` gets used exactly as F9
describes it: `GET /api/audit/{tracking_id}` is literally "give me
everything, from every service, that happened under this one id" — which
only makes sense as a dedicated aggregator, not bolted onto whichever service
happened to be first.

## Inputs / outputs

**Endpoints** (`app.py`):

| Method & path | Role required | Purpose |
|---|---|---|
| `GET /dapr/subscribe` | — (Dapr-internal) | Declares the six subscriptions below |
| `POST /events/{topic}` (×6) | — (Dapr-internal) | Records the event into metrics + audit |
| `GET /api/reports/summary` | `approver` | Dashboard numbers (F8) |
| `GET /api/audit/{tracking_id}` | `admin` | Full decision trail (F9) |

**Publishes:** nothing — like notification, a pure sink.

**Subscribes to exactly six topics** (`app.py:26`): `item.submitted`,
`decision.made`, `escalation.requested`, `escalation.resolved`,
`payment.completed`, `payment.failed`. Worth being precise here against
`docs/ARCHITECTURE.md`'s looser phrasing ("reporting subscribes to every
topic") — it doesn't, quite. **`item.status-changed` and `payment.requested`
are deliberately left out**: every status change is already implied by one of
the six tracked topics, and the payment *outcome* topics
(`payment.completed`/`payment.failed`) already capture what
`payment.requested` only announces the start of. Subscribing to all eight
would just double up entries in the audit trail for no new information.

**State it owns:** `metrics` (one document — see below), `audit::{tracking_id}
→ list[AuditEntry]`.

## Non-obvious design choices

**1. The metrics document bundles the counters *and* the dedupe record into
one document, on purpose.** `MetricsState` (`app.py:36`) wraps `Metrics`
together with `applied: list[str]` (event keys already counted) — so "have I
already counted this event" and the actual counter mutation commit as **one**
atomic write (`update_metrics`, `service.py:61`). This is the fourth distinct
expression of the same M10 principle you'll have seen by now: intake's atomic
claim, the Workflow engine's instance-id dedup, payment's pre-write marker,
and now reporting's (and notification's) same-document dedupe — four
different shapes of "the dedupe fact and the effect must commit together, or
a crash between them creates a gap." Full side-by-side in
`09-cross-cutting.md`.

**2. The applied-key list is capped at 1000 entries**
(`_APPLIED_CAP`, `app.py:47`), with an explicit, honestly-stated reason: a
redelivery arriving after 1000 subsequent events "is beyond RabbitMQ's
practical redelivery horizon here." This is a pragmatic bound, not a
rigorous one — worth being able to name the trade-off (unbounded correctness
vs. unbounded storage growth) rather than presenting it as airtight.

**3. Metrics are computed from the raw event payload by topic name, not
against a strict shared schema.** `_apply()` (`service.py:129`) reads
`payload.get("route")`, `payload.get("usdAmount")`, etc. directly, rather
than validating each payload into a specific Pydantic model first. The
comment states the intent plainly: *"new event fields never break
reporting."* That's a deliberate trade of a little type safety for
resilience against schema drift elsewhere — reporting won't reject or crash
on an event just because some other service added a field to its payload.

**4. Schema migration for a live upgrade is handled explicitly, not just
assumed.** `DaprReportStore._load_state` (`app.py:60`) recognizes and
migrates a pre-`MetricsState` "flat `Metrics`" document (no `metrics`
wrapper) into the current shape, so upgrading the reporting service's code
against an already-running system doesn't reset every dashboard counter to
zero. This is a genuinely "we thought about upgrading a live system" detail,
covered directly by `test_legacy_flat_metrics_document_is_migrated`.

## If this crashes right now

- `record()` (`service.py:107`) calls `append_audit` **then**
  `update_metrics`. A crash between them leaves an audit entry with no
  matching count yet — but the handler never returned `200`, so Dapr
  redelivers, and the retry's `append_audit` is a no-op (already there) while
  `update_metrics` proceeds normally (that event key isn't in `applied` yet)
  and completes the count. Net effect: correct, just delayed to the
  redelivery — proven directly by `test_redelivered_event_does_not_double_count`
  and `test_failed_metrics_update_is_retried_by_redelivery`.
- If `update_metrics`'s own internal retry loop exhausts all 8 attempts
  against persistent ETag contention, it raises `RuntimeError`, which
  propagates to the HTTP handler as a `5xx` — Dapr treats that as delivery
  failure and redelivers, same as any other crash mid-handler.
- **If reporting is down entirely, nothing else in the pipeline notices or
  waits for it.** Dashboard and audit data simply stop updating for that
  window — no other service reads reporting's state or blocks on it, so
  submission → decision → payment continues completely unaffected. Once
  reporting is back, RabbitMQ's durable, unacked backlog is redelivered and
  it catches up.
