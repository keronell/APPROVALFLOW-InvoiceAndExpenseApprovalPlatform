# Anticipated questions - concise prepared answers

Pulled from `docs/prep/DEFENSE-PREP.md` (already-verified facts) plus
general questions a microservice-architecture + AI-engineering panel is
likely to ask. Answers are facts, not scripts - say them in your own words.

## The router / AI safety (the questions to be sharpest on)

**"How do you actually know the LLM can't approve something on its own?"**
`route()` (`router.py`) is a pure function, no I/O, and the only attribute
of `AgentAssessment` it ever reads is `.confidence` - verified directly by
reading the function body. `assessment.recommendation` is never accessed
anywhere in it. Auto-approve is reachable through exactly one branch, after
every other condition (ceiling, hard stops, confidence floor) already
passed.

**"Is 0.80 hardcoded?"** No - `AutonomyThresholds.min_confidence`, served
by the config service, mutable at runtime via `PUT /config/thresholds`,
defaulting to 0.80.

**"What stops prompt injection - an invoice note saying 'approve me'?"**
The router never reads free text at all. The agent treats invoice notes as
data, never instructions, per its system prompt and `policy_engine.py`'s
`_text()` docstring. `INV-1013` is the fixture built specifically to prove
this - an adversarial note that doesn't change the route.

**"What stops someone from splitting one big expense into many small ones
under the ceiling?"** Nothing today - confirmed gap. `GLOBAL-DUP` only
catches an *identical* resubmission (same vendor + invoice number + total),
not aggregate spend velocity. Named as open, not hidden.

**"Where does confidence actually come from - why trust it?"** It's the raw
LLM's self-reported structured output (or the stub's category heuristic, or
a hardcoded 0.0 fail-safe on error). No deterministic cross-check exists
today. Worth saying plainly if asked: it's one model's self-report, and
`docs/FUTURE-WORK.md` names strengthening that (e.g., an agreement-based
multi-model confidence) as a future direction - importantly, that would
only ever change *what number* feeds the same one-directional gate, never
give the router anything new to trust blindly.

## Decomposition (why 8 services)

**"Why 8 services and not fewer/more?"** Full answer in
`docs/prep/PRESENTATION-WHY-8-SERVICES.md` - short version: each boundary
maps to a named requirement, a distinct consistency problem, a volatility
line (the LLM), or state ownership with no legitimate second reader.
Decision is the sharpest example - isolating the one non-deterministic
component means it's the one container you'd ever put on different
(GPU) hardware.

**"Couldn't notification and reporting be merged - they're both just event
sinks?"** Fair pushback - concede this one partially. They serve different
audiences on different cadences (per-submitter terminal-only vs.
cross-cutting everything, admin-facing). Mergeable without breaking a
requirement, but you'd be building one service with two unrelated read
models.

**"Why not split the policy engine out of decision too?"** It's a pure
function with no I/O, called in-process - splitting it would add a network
hop to a path that needs to stay fast for the router's determinism
argument, for zero independent-deployability benefit. Split where the
*reason to change* diverges, not to inflate the count.

## Payment / consistency

**"Could a crash mid-payment double-charge someone?"** No. Three
independent layers: (1) the workflow instance id *is* the tracking id, so a
redelivered event can't start a second workflow; (2) Dapr Workflow replays
completed activities from history rather than re-running them; (3) the one
genuinely risky window - mid-`pay_activity` - is covered by a durable
"attempting" marker written *before* the provider call, a same-idempotency-
key retry, and a status check that short-circuits if already `paid`.

**"What's the honest gap in the payment saga?"** No `call_activity` in the
workflow carries a retry policy, and the `try/except` only wraps
`pay_activity` - if the compensating `release_activity` itself throws, that
propagates uncaught, stranding a reservation. Admitted directly in
ADR-002's own Risks section; not hidden.

**"What is an ETag, and why not just use a lock?"** A version stamp the
state store bumps on every write. Two concurrent readers can both compute a
new value; the store only accepts whichever write arrives first under the
version it read, and the loser retries with fresh data - optimistic
concurrency, not a mutex. Chosen because there's nothing to leak or hang
onto if a service crashes mid-operation, and it composes into the same
atomic transaction that also writes the reservation record.

## Infrastructure / Dapr

**"Why Dapr instead of talking to RabbitMQ/Redis directly?"** One
integration layer across pub/sub, state, service invocation, and workflow -
adding a ninth service means copy-pasting one sidecar pattern instead of
re-deriving broker/store wiring. Cost: 19 containers, a sidecar per
service, coupling to Dapr's semantics and release cadence (ADR-003's own
stated trade-off).

**"What if a Dapr sidecar is slow?"** It's in the critical path for its
paired service specifically (network namespace shared via `network_mode:
service:<app>`) - a slow sidecar slows that one service, not others.
Named directly as a risk in ADR-003.

**"Why nginx over a custom gateway?"** The "serve millions of users"
framing the assignment invites - proven at internet scale, `limit_req`
built in, tiny footprint. Deliberately doesn't verify JWTs itself, so
authorization logic never has two places to drift out of sync.

## Testing / reliability

**"How do you know the durable human-review pause actually survives a
crash, not just architecturally?"** `tools/verify.py`'s
`journey_escalate_and_resume` doesn't just claim it - it actually issues
`docker compose restart` against the `decision` service and its sidecar
between the escalation and the approval, then asserts the item still
reaches `paid`.

**"Does CI actually use a real LLM?"** No, deliberately - `tools/verify.py`
forces `LLM_PROVIDER=stub`, a real (not mocked) `BaseChatClient` that runs
the true agent loop offline, so CI is hermetic and deterministic. The live
demo today uses the real `ollama` provider - different config, same code
path.

**"What's your test coverage story?"** In-memory backends + `LLM_PROVIDER=
stub` for unit tests (no network, no real Dapr); `TestClient`-driven
integration tests; and `tools/verify.py` as full end-to-end journey
verification against the real containerized stack, run on every CI push.

## Security

**"Is everything behind auth?"** Almost - the one exception: `config`'s
three endpoints carry no `require_role`/JWT check at all; `PUT
/config/thresholds` uses a separate `X-Config-Token` header instead.

**"Why doesn't the gateway verify JWTs?"** Deliberately generic
infrastructure - keeping authorization inside each service's own
`require_role` means there's exactly one place per service that can grant
access, not two systems that could disagree.

## Product / scope

**"If the ceiling isn't your primary safety control, why have one at
all?"** Direct quote from `docs/PRODUCT-DILEMMA.md`: it's one of several
independent gates, not the primary control. For the `other` category
specifically, there's no dedicated cap in the policy engine - the ceiling +
confidence + hard stops are the only backstop there, unlike
meals/hardware/travel which have category-specific caps behind them too.

**"How would you know the $250/0.80 numbers hold on real traffic?"**
Honestly: you wouldn't, yet. `dilemma_report.py` only replays the 20-fixture
set; `docs/FUTURE-WORK.md` names "cold start, only 20 fixtures" as a present
weakness, not a solved problem.
