# Key decisions

Six decisions worth having ready, each with the one-line rationale and the
file to open if the conversation goes deeper (see
`05-code-navigation-cheatsheet.md` for exact paths/lines).

## 1. The router only ever reads `confidence`, never `recommendation`

The router - `route()` in `router.py` - is the entire answer to "how do you
stop an LLM from approving money." It's a pure function with no I/O, and
the only attribute of the agent's output it ever touches is `confidence`,
used strictly to downgrade toward a human. Even a forced confidence of 1.0
on an over-ceiling item still escalates - verified by
`test_forced_approve_over_ceiling_still_escalates`.
**ADR:** `docs/adr/adr-001-policy-engine-router-split.md`.

## 2. Saga, not two-phase commit, for payment

Payment reserves budget, pays, then confirms - with a compensating release
if payment fails - instead of a distributed transaction, because there's no
shared coordinator across RabbitMQ and two independently-owned Redis stores
to enroll as 2PC participants. The trade-off: compensation itself isn't
retried automatically today - a real, named risk in the ADR, not a hidden
one.
**ADR:** `docs/adr/adr-002-saga-over-2pc.md`.

## 3. Dapr as the one integration layer

Every service talks to pub/sub, state, and workflow through Dapr sidecars
instead of direct RabbitMQ/Redis client libraries. The cost is real - 19
containers, a sidecar per service, a framework dependency - but it means
adding a ninth service is copy-pasting one pattern, not re-deriving
broker/store wiring from scratch.
**ADR:** `docs/adr/adr-003-dapr-as-integration-layer.md`.

## 4. Config fallback fails safe, never fails open

If the config service is down, decision doesn't crash and doesn't loosen
the ceiling - it falls back to hardcoded defaults that match the shipped
$250/0.80. The honest caveat: that's safe relative to what shipped, not
relative to a stricter value a human operator set at runtime - an outage
can't widen the ceiling past default, but it can undo a tightening.
**ADR:** `docs/adr/adr-004-config-fallback-fail-safe.md`.

## 5. Eight services, each earning its boundary for a named reason

Decision is the clearest example - it's the one non-deterministic component
(the LLM), isolated behind a process boundary specifically so it can be
redeployed, scaled, or put on different hardware without touching intake,
payment, or approval. Every other split maps to a requirement or a distinct
consistency problem, not an even feature split.

## 6. Optimistic concurrency (ETag/CAS), not locks, for the budget ledger

Two concurrent reservations against the same budget don't block each
other - they both read, both try to write, and the state store only accepts
whichever write lands first under the version tag it read; the loser just
re-reads and retries. No lock manager, nothing to leak if a service crashes
mid-operation, and it composes into the same atomic transaction that also
writes the reservation record.
**File:** `services/payment/approvalflow_payment/budget_dapr.py:28-48`.

---

## The one to go deepest on

Decision #1, the router. It's the shortest, simplest file in the system,
fully test-covered, zero I/O - and it's the literal answer to the project's
hardest constraint (M12/F10: the autonomy ceiling must be provably
impossible to exceed). Nothing else in the repo is as clean a "walk me
through exactly why this is safe" answer.
