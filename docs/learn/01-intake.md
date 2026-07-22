# Intake

Package: `services/intake/approvalflow_intake/` · App id: `intake` · Port `8001`
(loopback) · fronted by the gateway at `/api/invoices`, `/api/auth`.

## What it owns, and why it's a separate service

Intake owns exactly one thing: **the submitter-visible lifecycle of a
submission** — `SubmissionRecord` (`store.py:24`), keyed by tracking id, plus
two dedup namespaces (`fp::{fingerprint}` and `idem::{key}`). No other service
ever reads or writes intake's state (per-service `keyPrefix`, see
`09-cross-cutting.md`).

It's split out from decision for one reason that maps directly to a
requirement: **F1/M8 say submission must never block on processing.** If
intake and decision were one service, `POST /api/invoices` would have to wait
on the LLM call and the workflow scheduling before it could return — exactly
what F1 forbids. By making intake a thin, fast, synchronous front door that
does only *validate → dedupe → persist → publish*, the 202 comes back in
milliseconds regardless of how slow the agent or the human approver is.

A second, less obvious reason: intake is where **duplicates die before they
cost anything**. Dedup happens here, before `item.submitted` is ever
published — so a duplicate never triggers a second agent call, a second
workflow, or a second payment. Putting that check anywhere downstream (e.g.
inside the decision workflow) would mean paying for an LLM call and workflow
scheduling on every duplicate before discarding the result.

Third: intake is the **submitter's single query surface** (F2). It keeps its
own denormalized `status` field, updated by subscribing to
`item.status-changed`, rather than making the submitter (or the UI) reach into
decision/payment/approval's internals to figure out what happened. Those
services' internal state shapes can change freely without touching the
submitter-facing contract.

## Inputs / outputs

**Endpoints** (`app.py`):

| Method & path | Role required | Purpose |
|---|---|---|
| `POST /api/invoices` | `submitter` | Accept + dedupe + publish (F1) |
| `GET /api/invoices/{tracking_id}` | any authenticated | Status + plain-language reason (F2) |
| `POST /api/auth/token` | none (dev issuer) | Mints the self-signed role JWT (N1) — happens to be mounted here |
| `GET /dapr/subscribe` | — (Dapr-internal) | Declares the subscription below |
| `POST /events/status-changed` | — (Dapr-internal) | Applies downstream status updates |

**Publishes:** `item.submitted` (`Topics.ITEM_SUBMITTED`) — exactly once per
genuinely new invoice, never for a duplicate or a replayed idempotency key.

**Subscribes:** `item.status-changed` — every other service's status updates
flow back through here so `GET /api/invoices/{id}` stays current.

**State it owns** (Dapr `statestore`, prefixed `intake||`):
`{tracking_id} → SubmissionRecord`, `fp::{vendor|invoiceNumber|total} →
owning tracking_id`, `idem::{Idempotency-Key} → owning tracking_id`.

## Non-obvious design choices

**1. The tracking id isn't generated here — it's whatever correlation id
already exists by the time the request arrives.** `submit_invoice`
(`app.py:54`) does `correlation_id = get_correlation_id() or
new_correlation_id()`, and `IntakeService.submit` sets `tracking_id =
correlation_id` (`service.py:39`) directly — no separate id generation. In
practice that value was minted upstream by nginx's `$request_id` (or supplied
by the caller via `X-Correlation-Id`), so the tracking id, the correlation id,
and "the id nginx assigned this HTTP request" are all the same value, by
construction, from the first hop onward. A caller can even pre-choose their
own tracking id by setting the header themselves
(`test_submitter_supplied_correlation_id_becomes_tracking_id`).

**2. Two dedup mechanisms exist at once, and they protect against different
things.** The `Idempotency-Key` header (`app.py:57`) protects a *client's own
retry* — "did I already fire this exact POST" — scoped to whatever key the
caller chooses. The fingerprint (`vendor + invoiceNumber + total`,
`InvoiceSubmission.fingerprint`, `contracts.py:116`) protects the *business
identity* of the invoice — "has this real-world invoice been submitted by
anyone, ever" (GLOBAL-DUP). They're checked in that order in `submit()`
(`service.py:33`), and the idempotency check has a race baked in on purpose:
if a key is claimed but the owner's record isn't persisted yet (the original
request is still in flight), the retry does **not** fall through and publish
a second time — it returns an "accepted" response pointing at the owner and
does zero new work (`service.py:51-60`, proven by
`test_idempotency_key_claimed_but_record_not_yet_persisted`). Getting this
wrong (falling through instead) is exactly how you'd end up with two
publishes for one logical submission.

## If it crashes right now

- **Mid-request, between `store.put(record)` and `publisher.publish(...)`**
  (`service.py:93-100`): the `SubmissionRecord` is durably saved
  (`status=RECEIVED`) but `item.submitted` never goes out, so decision never
  runs — that item is stuck at `received` forever. This sounds alarming but
  check the blast radius: the HTTP response (the 202 + tracking id) is built
  and returned only *after* both calls succeed, so if the process dies in this
  window the caller's connection drops before they ever see a tracking id —
  they have no way to even ask about the orphaned record. It's a lost
  submission requiring resubmission, not a double-charge or duplicate-payment
  risk (M9/M10 are about downstream money effects, which never started). This
  is precisely the gap an outbox pattern (N3) would close, and N3 was
  explicitly not attempted — worth naming as a known, honest trade-off rather
  than something to paper over.
- **Already-accepted submissions are unaffected by an intake restart.** The
  store is Redis-backed via Dapr (`STORE_BACKEND=dapr` in compose), external
  to the intake process, so a crash-and-restart loses nothing already
  persisted — `fp::`/`idem::` claims and every `SubmissionRecord` are exactly
  as they were.
  - The in-memory backend (`InMemorySubmissionStore`, used only in tests /
    `STORE_BACKEND=memory`) *would* lose everything on restart — that's a
    deliberate test-only trade-off, never how docker-compose runs it.
- **While intake is down:** new submissions fail outright (`POST
  /api/invoices` has nowhere to land), and — easy to miss — **no one can sign
  in**, because the dev JWT issuer (`POST /api/auth/token`) is mounted on
  intake too. `GET /api/invoices/{id}` (the plain status card) also breaks.
  What *doesn't* break: items already past intake keep moving — decision,
  approval, and payment all subscribe/publish independently and don't call
  back into intake to make progress. The admin audit trail
  (`GET /api/audit/{id}` on reporting) is a separate service and stays
  reachable even while intake is fully down.
