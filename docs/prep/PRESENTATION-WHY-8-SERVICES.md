# Presentation notes — "why did you split into 8 services specifically?"

Talking points for defending the decomposition. Every claim below traces to
a "What it owns, and why it's a separate service" section already written in
`docs/learn/01-intake.md` through `08-gateway.md` — this file just pulls
those eight arguments into one place and adds the trade-off framing an
interviewer is actually probing for. Nothing here is invented; it's a
synthesis.

---

## The one-sentence version

Each boundary earns its keep for a *specific, nameable* reason — a
requirement that forces independence, a consistency problem that doesn't
belong anywhere else, or a volatility/ownership line — not "microservices
are trendy" or an even split of features. If I can't say *why this one*, it
shouldn't be its own service.

---

## The four decomposition criteria actually used

Every one of the 8 splits falls into one (sometimes two) of these buckets.
Naming the bucket is what makes the answer sound principled instead of
post-hoc:

1. **A requirement literally forces process independence.** F1/M8 say
   submission must never block on processing — that's not satisfiable by a
   module boundary inside one process, because the caller would still wait
   on the same event loop. It needs to be a different container that can
   return a 202 while another container is still thinking.
2. **A distinct consistency/failure problem lives here and nowhere else.**
   Concurrent approver clicks, saga compensation, exactly-once workflow
   resume — each of these is a genuinely different correctness discipline
   that would tangle unrelated code together if it lived inside a service
   that has other jobs.
3. **A volatility or ownership boundary.** The one non-deterministic
   component (the LLM) and the one infrastructure-only concern (routing/
   rate-limiting) both get isolated so their *reason to change* — a new
   model, a new rate limit policy — never forces a redeploy of anything
   that isn't itself.
4. **State ownership with no legitimate second reader.** If nothing else in
   the system should ever need to read a piece of state directly, that's a
   service boundary, not an internal module — otherwise you're one refactor
   away from someone reaching into another service's store.

---

## The 8, one line each

| Service | Criterion | The argument |
|---|---|---|
| **intake** | (1) requirement | F1/M8: submission can't block on the LLM/workflow; also where duplicates die before they cost an agent call |
| **decision** | (3) volatility | The *only* non-deterministic component (the LLM) gets a hard process boundary — redeploy/scale/repoint-the-model without touching intake/payment/approval; the one container you'd put on GPU-adjacent infra |
| **payment** | (2) consistency + (4) ownership | "Should this be approved" (policy) vs. "move money, never overspend" (ledger) are different disciplines; owns the only budget ledger, doesn't care *why* it was told to pay |
| **approval** | (2) consistency | A genuinely different problem — concurrent human clicks, retries, exactly-once resume — that `decision` shouldn't have to know about |
| **notification** | (1) requirement | M8's "notify later" is a different shape of concern than F2's "let me check status now"; addressed by identity, not tracking id |
| **config** | (1) requirement | F7/M13: change policy *without a redeploy* — a runtime-mutation concern, cleanly separate from *enforcing* policy |
| **reporting** | (4) ownership | Cross-cutting history (audit trail, metrics) has to live somewhere that isn't any single operational service, or that service ends up owning data it doesn't need for its own job |
| **gateway** | (3) infra boundary | M6: single external entry point + rate limiting has no natural home inside any backend service; keeping it dumb (no JWT verification) keeps authorization in exactly one place per service instead of two that can drift |

---

## The trade-off, stated plainly (this is the part worth saying unprompted)

**What 8 services costs:** 19 containers for one system (`docker compose
ps`), a Dapr sidecar per service, cross-service calls that are now network
hops instead of function calls, more failure modes to reason about
(`docs/learn/12-what-if-a-service-is-down.md` exists *because* this is a
real cost), and slower local iteration than one process would give you.

**What it buys:** every line in the table above — independent
deploy/scale/failure domains that map to real requirements, not just
aesthetic separation. The concrete proof it wasn't over-engineering for its
own sake: `tools/verify.py`'s `journey_escalate_and_resume` actually kills
and restarts the `decision` container mid-flight and the system recovers —
that's the payoff of the boundary being real, not diagrammed.

**The honest caveat:** at this scale (a capstone, not a production system
under real load), a well-modularized monolith would satisfy every
functional requirement too. The reason to still take the microservice
architecture at face value here is that the *assignment itself* is scoped
around demonstrating service boundaries, independent deployability, and
Dapr as an integration layer (see ADR-003) — the decomposition is answering
the assignment's actual question, not solving a scaling problem this system
doesn't yet have.

---

## If pushed: "couldn't you merge some of these?"

Have one candidate ready to concede partially and one to defend hard —
sounds more honest than defending all 8 as untouchable.

- **Notification + reporting** are the pair most likely to draw "aren't
  these the same thing?" — both are pure event sinks with no publishes.
  The real answer: they serve different audiences on different cadences —
  notification is per-submitter, terminal-states-only, "tell me when mine
  is done"; reporting is cross-cutting, every-event, admin/approver-facing,
  "show me everything." Mergeable without breaking a requirement, but you'd
  be building one service with two unrelated read models and two unrelated
  audiences — the split is defensible, not sacred.
- **decision** is the one to defend hardest, not concede: the GPU-adjacent
  argument is concrete, not aesthetic — it's the one place in the system
  where "a different piece of hardware might run this container" is a real
  operational fact, not a hypothetical.

## If pushed: "why not split further?" (e.g. pull the policy engine out of decision)

Facts to use: `policy_engine.py` is a pure function with no I/O, called
in-process by `decision_core.py` — splitting it into its own service would
add a network hop to the one path that's already required to stay fast and
side-effect-free for the router's determinism argument (ADR-001), for zero
new independent-deployability benefit — nothing would ever redeploy the
policy engine without redeploying the router it's paired with. That's the
line: split where the *reason to change* actually diverges; don't split
just to make the count bigger.
