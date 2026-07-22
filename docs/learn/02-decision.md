# Decision

Package: `services/decision/approvalflow_decision/` · App id: `decision` ·
Port `8002` (loopback) · not proxied by the gateway except indirectly (the UI
never calls it directly — everything it does is either event-driven or
service-invoked by `approval`).

## What it owns, and why it's a separate service

Decision owns **the one moment in the whole system where AI output gets
anywhere near a money decision**, and it owns the orchestration (Dapr
Workflow) that makes that moment either instant or a durable pause for a
human. Concretely: `PolicyEvaluation`, `AgentAssessment`, `RouteDecision`, and
the persisted `DecisionRecord` (`decision::{tracking_id}` in its own state
store) are all decision's.

Splitting this into its own service is ADR-001's whole argument made
physical: the system needs a **volatility boundary** around the one
non-deterministic component (the LLM), and making that boundary a process/
deployment boundary — not just a module boundary — means the "AI-ness" of
ApprovalFlow is entirely contained in one container. It can be redeployed,
scaled, or pointed at a different model without touching intake, payment, or
approval at all. It's also the one service where that matters operationally:
if you ever ran a live model instead of the stub, this is the container you'd
put on GPU-adjacent infrastructure — a concrete decomposition argument, not
just tidiness.

The second reason: decision is where the **two different shapes** of the
decision lifecycle — straight-through (auto-approve/reject/duplicate) and
pause-and-resume (human review) — are unified under one Dapr Workflow. That
orchestration responsibility belongs with whoever computes the decision, not
with `intake` (which shouldn't need to know what a workflow pause is) or
`approval` (which shouldn't own money-relevant routing logic).

## Inputs / outputs

**Endpoints** (`service/app.py`) — note none of these carry a `require_role`
guard, and that's deliberate: `gateway/nginx.conf` explicitly does **not**
proxy `/api/decisions` (see its inline comment) because this is a
Dapr-internal surface reached only via service invocation from `approval`,
never from a browser.

| Method & path | Purpose |
|---|---|
| `GET /dapr/subscribe` | Declares the subscription below |
| `POST /events/item-submitted` | Schedules `decision_workflow` |
| `GET /api/decisions/{tracking_id}` | Read the persisted `DecisionRecord` |
| `POST /api/decisions/{tracking_id}/human-decision` | `approval` calls this via Dapr service invocation to resume a paused workflow (F5/M11) |

**Publishes:** `decision.made`, `item.status-changed` (always); `payment.requested`
(only when the route is or becomes `auto_approve`/human-approved);
`escalation.requested` (only for `human_review`, carrying the agent's
rationale for F4).

**Subscribes:** `item.submitted`.

**Also does synchronous Dapr service invocation** (not pub/sub): calls
`GET config/thresholds` on the `config` app id per item, and is called *by*
`approval`'s `POST .../human-decision`.

**State it owns:** `decision::{tracking_id} → DecisionRecord`, plus the Dapr
Workflow runtime's own actor-backed orchestration state (instance history,
the suspended-on-`wait_for_external_event` fact) — same Redis `statestore`,
different keys, managed by the `dapr-ext-workflow` SDK rather than by this
code directly.

---

## §1 — The deterministic core: `policy_engine.py` + `models.py`

`evaluate(invoice, thresholds) -> PolicyEvaluation` is a pure function: no
LLM, no I/O, no randomness, no clock reads beyond the invoice's own `date`
field. It converts the total to USD, checks math reconciliation, and applies
every rule from policy.md §§1–5, appending a typed `PolicyFinding(rule_id,
severity, message)` for each violation. `Severity` is an `IntEnum` (`INFO=0,
HARD_STOP=1, HIGH=2`) specifically so `PolicyEvaluation.hard_stops` and
`.rejections` can filter by it — there's no separate "is this bad" boolean
anywhere, just this ordering.

Two things worth being precise about:

- **An unsupported currency is a hard stop, not a 1:1 guess.**
  `_usd_amount` (`policy_engine.py:25`) does fall back to a `1:1` rate
  internally, but only so the hard-stop message has *some* number to print —
  `evaluate` separately raises `GLOBAL-FX-UNKNOWN` for any currency not in
  `thresholds.fx_rates`, which forces human review regardless of that
  fallback number. A silent 1:1 guess could otherwise let a large foreign
  amount slip under the ceiling. (`test_unknown_currency_is_hard_stop`.)
- **Category caps compare against the converted USD amount, never the raw
  original-currency figure** — `_evaluate_category` receives `usd` as a
  parameter and checks against it, not `invoice.total`. This matters
  concretely: a €199 SaaS subscription converts to ~$215, which *is* over the
  $200/mo cap, even though 199 alone would look fine
  (`test_saas_cap_compared_in_usd_not_original_currency`).

Also worth noting: `_text()` (`policy_engine.py:43`) lower-cases the notes and
line-item descriptions for keyword checks (`"alcohol"`, `"client"`, `"first
class"`) — and its own docstring states the rule explicitly: this text is
used *only* to **add** scrutiny (flag alcohol, flag "client" for the MEAL-02
check), never to remove it or to grant anything. It's the policy engine's
half of the prompt-injection defense described in `CLAUDE.md`; the router's
half is that it never reads free text at all.

One real limitation to be able to name: **MEAL-02 ("client entertainment over
$500 needs a client name") is detected by the keyword `"client"` in free
text**, because the submission schema has no structured client-name field.
The engine can't verify a client name was *provided* — it can only notice the
word "client" appears and treat that as ambiguous-enough to require human
judgment. That's a real gap between what policy.md asks for and what the data
model can currently represent.

## §2 — The router: `router.py` (read this one in full)

`route(evaluation, assessment, thresholds, *, already_processed=False) ->
RouteDecision` is ~80 lines, pure, and the entire proof of M12/F10. Its shape:

1. `already_processed` → `DUPLICATE`, citing `GLOBAL-DUP`.
2. Any `HIGH`-severity finding (currently just `MEAL-03`, alcohol) →
   `REJECT`, at any amount — a deterministic rejection always outranks
   whatever the agent recommended.
3. Otherwise, collect **every** reason auto-approval is blocked into one
   `blockers` list — hard stops, category non-compliance (only added if there
   isn't already a hard stop explaining it, so e.g. `HW-02` doesn't also
   print a redundant generic "not compliant" line), over-ceiling, and
   low-confidence — and if `blockers` is non-empty, `HUMAN_REVIEW` citing all
   of them together (so the approver sees every reason at once, not just the
   first one hit).
4. Only if `blockers` is empty: `AUTO_APPROVE`.

**The fact to be able to say cold:** of every field on `AgentAssessment` —
`category`, `risk`, `confidence`, `cited_rules`, `policy_violations`,
`fraud_signals`, `recommendation`, `reasoning` — `route()` reads exactly one:
`confidence`, and only to compare it against `thresholds.min_confidence` and
*downgrade* to human review if it's too low. `recommendation` is never read
here at all. That's not a simplification for this doc — grep the file
yourself; `assessment.confidence` is the only `assessment.` attribute access
in the whole function body. This is why `test_forced_approve_over_ceiling_still_escalates`
works: feeding the router a hand-built `AgentAssessment(confidence=1.0,
recommendation=APPROVE)` on a $5,000 item still escalates, because the
ceiling comparison uses `evaluation.usd_amount` (the policy engine's number)
and nothing about "confidence 1.0 + recommend approve" ever touches that
comparison or bypasses it.

**A subtlety worth catching:** `already_processed` is a real parameter with a
real branch and a real test (`test_already_processed_routes_duplicate`), but
grep for who actually *sets it to `True`* — it's only `tests/unit/test_router.py`,
`tests/unit/test_fixtures_expected_routes.py`, `tests/unit/test_agent_stub.py`,
and `tools/dilemma_report.py`. **The live, running system never calls
`route()` with `already_processed=True`.** As covered in `01-intake.md`, real
duplicate detection happens entirely inside `intake`'s fingerprint claim,
*before* `item.submitted` is ever published — so the decision workflow, and
therefore this branch of the router, never sees a duplicate in production.
The branch exists so the router's behavior is provable and testable in
isolation (and so `tools/dilemma_report.py` can replay `INV-1007` correctly
against the fixture set), not because it's reachable from a real request.

## §3 — The agent: `agent/`

`assessor.DecisionAgent` wraps a Microsoft Agent Framework `Agent`
(`instructions=..., tools=[fetch_policy, lookup_vendor]`). `.assess(invoice)`
serializes the invoice into a `<invoice>...</invoice>`-wrapped prompt and
calls `self._agent.run(prompt, options=ChatOptions(response_format=AgentAssessment))`
— structured output, not a JSON blob it then hopes to parse. `response.value`
does the parse/validate; **any** exception (network, malformed output,
whatever) is caught once and re-raised as `AgentUnavailableError` — nothing
here ever returns a guessed or default assessment silently (M15).

`client.build_chat_client()` is the one provider-specific line the project
advertises: `stub` returns `StubChatClient()` directly (no OpenAI import at
all on that path); `ollama`/`groq`/`gemini`/`openai` all construct the same
`OpenAIChatClient` with a different `base_url`/`model`/`api_key`, because
they're all OpenAI-compatible endpoints. An unrecognized provider string
raises `ValueError` immediately rather than silently falling back to
something.

`tools.py` has two `@tool`s, and it's worth knowing exactly how limited they
are today: `fetch_policy(category)` returns a **hand-written snippet dict**,
not a live read of `Examples/policy.md` — the file's own docstring calls this
out as "the file-first RAG seam (N5)"; real retrieval was never built (N5 was
not attempted). `lookup_vendor(name)` checks a **hardcoded set** seeded from
the fixtures' `vendorKnown: true` vendors — a stand-in for a real vendor
database.

That second point matters more than it looks: **`lookup_vendor`'s answer
never flows anywhere.** The router's `GLOBAL-VENDOR` hard stop is computed
entirely by the deterministic policy engine from `invoice.vendor_known` — a
field the *submitter* supplies on the request payload. The agent's
independent vendor lookup only informs the LLM's own `reasoning`/`cited_rules`
text (useful context for a human reviewing the escalation queue), but nothing
cross-checks it against what the submitter claimed. Nothing in the
deterministic path would catch a submission that simply asserts
`vendorKnown: true` for a brand-new vendor — worth having an honest answer
ready for if this comes up (see `10-likely-questions.md`).

`stub_client.StubChatClient` is a genuine `BaseChatClient` subclass, not a
mock — the real MAF `Agent` loop (including its tool-calling plumbing) runs
end-to-end against it, which is exactly why CI and `tools/verify.py` can
claim they exercise the real agent path, not a stand-in for it. It decides
purely from structured fields (`category`, `vendorKnown`, `total`, line-item
`description`s) and explicitly ignores the free-text `notes` field — modeling
an agent that cannot be steered by an "approve me" note.

`agent/__init__.py` is worth a look on its own: at import time it checks that
the installed `agent_framework` package still exposes `Agent`,
`BaseChatClient`, `ChatOptions`, `ChatResponse`, `Message`, and `Content`, and
raises `ImportError` immediately if any are missing. This is the fail-fast
guard `CLAUDE.md` refers to — a breaking MAF upgrade crashes the service at
startup, loudly, instead of failing confusingly deep inside a request months
later.

One real bug this repo actually hit, worth having as a concrete story: small
models sometimes answer the "confidence is 0.0–1.0" contract as a percentage
instead (e.g. `90` instead of `0.90`). `AgentAssessment`'s `field_validator`
on `confidence` (`models.py:110`) remaps any value in `(1, 100]` to a
fraction before validation, so a unit slip degrades gracefully instead of
throwing the whole structured-output parse into the fail-safe path — see
`test_percent_scale_confidence_is_normalized` and the actual fix commit
(`8bbdfa8`, "fix(agent): accept percent-scale confidence from the model").

## §4 — The Dapr Workflow: `service/workflow.py`, `decision_core.py`, `config_client.py`, `app.py`

`decision_core.decide(...)` is the plain orchestration — `evaluate()` →
`agent.assess()` → `route()` → build a `DecisionRecord` — kept Dapr-free
enough to unit-test directly (`test_decision_core.py`). If `agent.assess()`
raises `AgentUnavailableError`, it's caught right here and swapped for
`_fail_safe_assessment(invoice)`: `confidence=0.0`, `recommendation=ESCALATE`.
Notice what *doesn't* happen — there's no special-cased "agent is down"
branch in the router. Confidence `0.0` just naturally fails the existing
`confidence >= min_confidence` gate like any other low-confidence assessment
would, and the item routes to human review through the same code path as
everything else. That reuse is deliberate and worth calling out as a design
strength if asked "what happens when the LLM is down": the answer is boring,
in the best way — it's just a low-confidence item.

`workflow.py`'s `decision_workflow(ctx, item)` is the orchestrator, and it's
short on purpose:

```python
decision = yield ctx.call_activity(decide_activity, input=item)
yield ctx.call_activity(publish_outcome_activity, input={...})
if decision["route"] == Route.HUMAN_REVIEW:
    human = yield ctx.wait_for_external_event(HUMAN_DECISION_EVENT)
    decision = yield ctx.call_activity(apply_human_decision_activity, input={...})
return decision
```

The determinism rule in the file's own docstring is the fundamental rule of
*any* durable-execution engine (Dapr Workflow, Temporal, Step Functions —
same idea everywhere): **the orchestrator function may be replayed from the
start to reconstruct its state after a crash**, so it must be a pure,
deterministic sequence of `call_activity`/`wait_for_external_event` calls.
All actual I/O — the config fetch, the agent's HTTP call, publishing events,
writing state — happens inside `@wfr.activity`-decorated functions
(`decide_activity`, `publish_outcome_activity`, `apply_human_decision_activity`),
each a thin sync wrapper (`asyncio.run(...)`) around an async implementation.
If the orchestrator did real I/O directly, replaying its history after a
crash would re-execute that I/O — re-charging things, re-publishing events —
every time the engine reconstructs state. Activities, by contrast, have their
*results* recorded in history once and are not re-run on replay.

**The instance id is the tracking id**, for both `decision_workflow` here and
`payment_workflow` in the payment service. `on_item_submitted`
(`app.py:48`) checks `client.get_workflow_state(tracking_id)` before
scheduling — if a workflow with that instance id already exists, scheduling
is skipped entirely. That single check is what makes a redelivered
`item.submitted` a no-op: this is the Workflow-engine-flavored idempotency
mechanism, structurally different from intake's atomic-claim mechanism and
approval's lease mechanism (compared side by side in `09-cross-cutting.md`).

**The HITL pause is durability, not just an `await`.** `wait_for_external_event`
suspends the orchestrator, and Dapr Workflow — built on Dapr Actors — persists
that suspended state in the same Redis `statestore` (`actorStateStore: true`
in `dapr/components/statestore.yaml`). That's the entire mechanism behind
"survives a restart": the suspension isn't a Python coroutine sitting in this
process's RAM, it's a fact recorded in Redis that any instance of the
`decision` service can reconstruct by replaying the orchestrator's history up
to the wait point. `POST /api/decisions/{tracking_id}/human-decision` (called
by `approval` over Dapr service invocation) does `client.raise_workflow_event(
tracking_id, HUMAN_DECISION_EVENT, data=...)` — that call finds the durably-
waiting instance by its instance id (the tracking id, again) and resumes it
exactly at the `yield` point. `tools/verify.py`'s `journey_escalate_and_resume`
is the sharpest proof available: it actually `docker compose restart`s the
`decision` and `decision-dapr` containers *between* the pause and the
approve call.

Small operational detail worth having ready: `decision` (and `payment`, the
other Workflow user) are the only two app containers in `docker-compose.yml`
that set `DAPR_GRPC_PORT` explicitly — the Workflow SDK talks to the sidecar
over gRPC, not just the plain HTTP that `DaprIO` uses everywhere else, so
those two services need it configured.

`config_client.fetch_thresholds` is called inside `decide_activity` — see
`06-config.md` and ADR-004 for the fail-safe; the short version is that any
failure reaching `config` (network error, bad data) is caught, logged as
`thresholds_fetch_failed`, and falls back to hardcoded `$250`/`0.80`, never
crashing the activity.

## If this crashes right now

- **Mid-orchestration** (e.g. the container dies right after `decide_activity`
  completes but before `publish_outcome_activity` runs): on restart, the
  Workflow runtime reconnects and replays the orchestrator's history.
  `decide_activity`'s result is already durably recorded, so it is **not**
  re-run (no second agent call, no second policy evaluation) — the
  orchestrator resumes and calls `publish_outcome_activity` fresh. This is
  the same general replay mechanism `journey_escalate_and_resume` proves for
  the pause case specifically.
- **`decision` is down when `item.submitted` is published:** not lost — the
  RabbitMQ pub/sub component is configured `autoAck: false`,
  `requeueInFailure: true` (`dapr/components/pubsub.yaml`), so delivery is
  retried once a subscriber is listening again. If decision had actually
  already scheduled the workflow before dying (crashed after scheduling but
  before acking), the `get_workflow_state` check makes a redelivery a no-op
  rather than a second workflow.
- **`decision` is down while an item is paused on a human:** this is the case
  the whole design optimizes for — no problem at all. The approver can act
  hours or days later, against a `decision` instance that may not even have
  existed at pause time (a redeploy, a version upgrade), and
  `raise_workflow_event` still finds and resumes the right instance because
  its state lives in Redis, not in any one process's memory.
- **`config` is unreachable during `decide_activity`:** falls back to the
  hardcoded safe defaults (ADR-004) — the item is still decided, just against
  the conservative shipped posture, never blocked.
- **The LLM provider is unreachable during `decide_activity`:** caught,
  substituted with the `confidence=0.0` fail-safe assessment, routes to human
  review — never crashes the workflow, never silently approves.
- **One failure mode with no per-request recovery:** if `agent/__init__.py`'s
  import-time guard trips (a MAF upgrade removed a required symbol), the
  entire `decision` service fails to start at all. That's deliberate — fail
  loud at deploy time — but it means the whole service is down, not
  gracefully degraded, until the code is fixed; there's no fallback for
  *that* failure mode the way there is for a merely-unreachable live
  provider.
