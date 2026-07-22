# Cross-cutting concerns

Concepts that span every service, best understood after you've seen all of
them individually — this is the synthesis pass.

## Dapr building blocks, by purpose

Every building block Dapr offers is used somewhere in this system, but not
interchangeably — each one is doing a job the others genuinely can't. The
question worth being able to answer live isn't "where is X used" but "why
that block and not another one, right there."

**Service invocation (sync)** — used at exactly two call sites in
application code: `decision → config` (`GET config/thresholds`,
`config_client.py`) and `approval → decision`
(`POST api/decisions/{id}/human-decision`, `approval/app.py`). Both share the
same shape: the caller needs a **specific, synchronous answer or a targeted
effect on one specific thing** before its own logic can continue. Decision
cannot route an item until it has thresholds (or has explicitly given up and
fallen back). The human's decision has to land on **one identified, already-
paused workflow instance** — not "whoever happens to be subscribed." Pub/sub
can't express "call this one thing and wait for its answer/target it
specifically"; invocation is built for exactly that, with the correlation id
forwarded as a header (`DaprIO.invoke`) so it stays traceable.

**Pub/sub (async)** — used for everything that's "announce a fact, let
whoever cares react," across 8 topics. The defining shape here is
**decoupled, sometimes fan-out, producers and consumers**: `item.submitted`
is consumed independently by *both* `decision` (start the workflow) *and*
`notification` (record who to notify later) — neither knows or cares that
the other exists. That fan-out is exactly what service invocation cannot do
(it's one caller, one specific callee) and exactly what pub/sub is for.

**State (Redis-backed, one `statestore` component)** — every service's own
memory, isolated by **`keyPrefix: appid`** (`dapr/components/statestore.yaml`)
so one service's keys are structurally invisible to another's — this
"services own their state" rule is enforced by infrastructure, not by
convention or code review. It also does double duty as Dapr Workflow's
**actor state store** (`actorStateStore: true`), which is *why* one shared
component backs both plain state and the two workflows — one stateful
backend to run and reason about instead of two.

**Secrets** — one real usage: the RabbitMQ connection string, resolved via
`secretKeyRef` in `pubsub.yaml` rather than embedded in the component file or
read directly by app code. The payoff of routing it through Dapr instead of
an env var read in Python: swapping `secretstores.local.env` (dev-grade) for
a real secret backend (Vault, cloud secret manager) is a **component-yaml
change, not a code change** — no service ever imports or parses a connection
string itself.

**Workflow** — reserved for the two flows that are genuinely long-running
and must survive a crash mid-flight: decision's HITL pause and payment's
saga. Pointedly **not** used for approval's queue, even though approval also
needs "don't do this twice" guarantees — a single atomic claim (PENDING→
RESOLVED) doesn't need a multi-step durable orchestrator; hand-rolled ETag
concurrency is simpler and sufficient there. Workflow buys you automatic
replay-based crash recovery across *multiple* steps, at the cost of the
determinism-in-orchestrator / I/O-in-activities discipline and the extra
`placement`+`scheduler` infrastructure it requires — worth it exactly twice
in this codebase, not more.

**Deliberately not used: Dapr's *configuration* building block** (ADR-003).
Thresholds live in plain state, served over service invocation by `config`,
specifically so there's **one** runtime-mutation path (`PUT /config/thresholds`)
instead of two systems that could disagree about the current value.

## Idempotency, end to end — one principle, six shapes

Every "don't do this twice" mechanism in this codebase reduces to the same
rule: **the fact "I already did this" must be recorded atomically with the
effect itself — never as a separate write before or after it.** Split those
into two writes and there's a crash window between them that either loses the
effect (dedupe-fact lands, effect never happens) or duplicates it (effect
happens, the dedupe-fact write is what's lost). Every row below is that same
rule, implemented against a different storage shape:

| Where | What "duplicate" means | Mechanism |
|---|---|---|
| Intake — submission | Same business invoice (vendor+invoiceNumber+total) submitted more than once | Atomic insert-if-absent claim (`try_insert_state`) on `fp::{fingerprint}`; the loser reads back the *winner's* tracking id instead of proceeding |
| Intake — client retry | Same HTTP request retried by the client | Same claim primitive on `idem::{key}`, with an explicit guard for "claimed but the owner's record isn't persisted yet" (`service.py:43-60`) |
| Decision / Payment workflows | A triggering pub/sub event (`item.submitted`, `payment.requested`) gets redelivered | Workflow **instance id = tracking id**; `get_workflow_state` checked before scheduling — dedup lives in the Workflow engine, not app code |
| Payment — the charge itself | A crash between the provider call succeeding and recording that fact | A durable `"attempting"` marker written **before** the call, carrying an idempotency key the provider itself deduplicates on retry |
| Approval — the human decision | Two concurrent clicks, or one click retried | Atomic PENDING→RESOLVED claim, **then** a separate side-effects lease with TTL-based crash recovery, replaying from the *stored* decision, never the retry's request body |
| Notification / Reporting — event consumption | An event redelivered after it was already processed | Dedupe key (`(tracking_id, status)`, or `topic::tracking_id`) checked and written inside the **same** conditional operation as the effect — no separate pre-claim step |

No two rows share code. That's not an oversight — each storage shape
(single key, workflow instance, external provider call, application-level
queue item, aggregate document) needs its own atomic primitive, and knowing
*why* they differ is a better answer than knowing that they do.

## Correlation id propagation — where it's automatic, and where it isn't

The origin is always the same: nginx's `$request_id`, or a client-supplied
`X-Correlation-Id`, resolved once at the gateway (`08-gateway.md`).

**Across synchronous HTTP hops — automatic.** `CorrelationIdMiddleware`
(`correlation.py:37`) reads the header, binds it to a `contextvar` for the
life of the request, and echoes it on the response; `DaprIO.invoke`
explicitly forwards it as a header on outbound service-invocation calls
(`daprio.py:135`). Every log line emitted during one of these hops picks up
the right id automatically, because structlog's log processor reads that
same contextvar.

**Across pub/sub hops — not automatic, and worth being precise about this.**
Dapr's pub/sub delivery to an `/events/*` route is its **own** new HTTP call
from the sidecar; it does not carry any custom header from the original
`publish()` call. Instead, every event contract (`ItemSubmitted`,
`DecisionMade`, `EscalationRequested`, `PaymentRequested`, `StatusChanged`,
`EscalationResolved`) carries `correlation_id`/`tracking_id` as an explicit
**data field**, and every handler reads it out of the validated payload and
threads it forward by hand — into further publishes, state writes, and log
calls.

Here's the subtlety, verified directly by grepping the codebase rather than
assumed: `set_correlation_id` — the function that seeds the logging
contextvar — is called **only** by `CorrelationIdMiddleware` itself. No
event handler anywhere manually re-seeds it from the payload's own
`correlation_id`. So for a log line emitted inside an `/events/*` handler (or
a workflow activity it kicks off), the *auto-attached* `correlation_id` field
reflects that specific delivery call's own id — not necessarily the business
item's tracking id — while `tracking_id` (or `correlation_id`) passed
**explicitly** as a kwarg to that `log.info(...)` call is the one that
actually matches. And every log call in this codebase does pass it
explicitly (`log.info("workflow_scheduled", tracking_id=submitted.tracking_id)`,
and so on throughout). That's not a convenience — it's the thing that
actually makes "grep one item's full journey across every service's logs"
work at all downstream of the first pub/sub hop.

Worth checking yourself rather than taking this on faith: bring the stack up,
submit `INV-1003`, and diff the auto-attached `correlation_id` field against
the explicit `tracking_id` field on a log line from inside `decision`'s
`on_item_submitted` handler. If they don't match, that's this mechanism made
visible; if you want the practical takeaway without checking, it's this:
**for F9/M14 tracing, filter logs on `tracking_id`, not purely on the
top-level `correlation_id` field, for anything downstream of the first hop.**

## Structured logging

`configure_logging` (`logging.py:18`) is called once, at service startup,
inside `create_app` — every service gets the identical pipeline: JSON lines
to stdout, through `merge_contextvars → add_log_level → TimeStamper(iso,
utc) → _add_correlation_id → StackInfoRenderer → format_exc_info →
JSONRenderer`. `bind_contextvars(service=service_name)` runs once at
configure time, so every line from that process carries which service
emitted it without any call site having to pass `service=` itself.

The practical shape of a log line is a flat JSON object, e.g. (structurally
representative, not captured output):

```json
{"event": "decision_made", "tracking_id": "a1b2...", "route": "auto_approve",
 "usd_amount": "42.00", "correlation_id": "a1b2...", "service": "decision",
 "level": "info", "timestamp": "2026-07-20T12:00:00Z"}
```

No string interpolation anywhere — every call site passes distinct keyword
arguments (`log.info("decision_made", tracking_id=..., route=...)`), which is
what "structured" buys you in practice: you filter and aggregate by field,
you don't grep a formatted sentence. It also ties directly to M15's "fail
loud, never silent": every caught-and-re-raised error in this codebase
(`AgentUnavailableError`, a config fetch failure, an exhausted retry loop)
logs `error=str(exc)` at `warning`/`error` level before surfacing — nothing
fails silently into a swallowed exception.

## Auth (N1), briefly

`approvalflow_shared/auth.py` is HS256 JWTs signed and verified with
`hmac`/`hashlib` from the stdlib — no JWT library dependency, a deliberate
minimalism the module's own docstring ties to keeping the `decision` image's
already-fragile pinned dependency resolution intact (see `constraints.txt`
and `10-likely-questions.md`). `require_role(*roles)` is one FastAPI
dependency reused by every service; `admin` passes every gate; missing/
invalid token → `401`, wrong role → `403`.

The trust boundary is stated explicitly in the module's own docstring, and
it's worth repeating precisely because it could look like an oversight if you
didn't know it was deliberate: **only user-facing routes reached through the
gateway carry `require_role`.** Dapr-internal surfaces — `/dapr/subscribe`,
every `/events/*` handler, decision's `/api/decisions/*`, every `/healthz` —
stay open. That's safe specifically *because* the gateway's routing table
(`08-gateway.md`) never proxies those paths; they're reachable only from
inside the compose/sidecar network, never from a browser. It's a boundary
enforced by network topology, not an auth gap.

The one deliberate exception to the whole scheme: `config`'s `PUT
/config/thresholds` uses a separate shared-secret header
(`X-Config-Token`) instead of a JWT role — covered in full in
`06-config.md`.
