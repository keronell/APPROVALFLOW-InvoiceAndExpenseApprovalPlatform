# ADR-003 - Dapr as the integration layer vs. hand-rolled equivalents

- Status: accepted
- Date: 2026-07-07

## Context

The assignment mandates Dapr for sync + async communication, state, and
secrets (M5). Within that mandate there was still a real design choice: how
much to lean on Dapr's building blocks versus hand-rolling the equivalents
(direct `httpx` calls to service URLs, a RabbitMQ client library such as
aio-pika, redis-py for state, env-var access for secrets, and a custom durable
state machine for the long-running flows).

How Dapr is actually used today (every service runs beside its own `daprd`
sidecar in docker-compose, sharing the network namespace):

- **Service invocation (sync):** decision → config
  (`GET config/thresholds`, `service/config_client.py`) and approval → decision
  (`POST api/decisions/{id}/human-decision`, `approval/app.py`), both through
  `DaprIO.invoke()` - no hardcoded peer URLs, correlation id propagated.
- **Pub/sub (async):** component `approvalflow-pubsub` (`pubsub.rabbitmq`)
  carries 8 topics (`item.submitted`, `decision.made`, `escalation.requested`,
  `escalation.resolved`, `payment.requested`, `payment.completed`,
  `payment.failed`, `item.status-changed`); services declare subscriptions via
  `GET /dapr/subscribe` and receive CloudEvents over HTTP.
- **State:** one Redis-backed `statestore` component with per-app `keyPrefix`
  (services cannot read each other's keys) and `actorStateStore: true`;
  payment additionally uses ETag first-write concurrency
  (`DaprIO.get_state_and_etag` / `try_save_state`) for the no-overspend
  guarantee.
- **Secrets:** `secretstores.local.env`; the RabbitMQ connection string is
  resolved via `secretKeyRef` in `dapr/components/pubsub.yaml` - no
  credentials in component yaml or code.
- **Workflow:** `decision_workflow` (durable HITL pause via
  `wait_for_external_event`, survives a service restart - M11) and
  `payment_workflow` (the saga of ADR-002). This building block is the reason
  the compose file runs `placement` and `scheduler`.

One deliberate simplification: Dapr's *configuration* building block is not
used. Thresholds live in the state store and are served over service
invocation by the config service (runtime-changeable via
`PUT /config/thresholds`, F7/M13). An unused `configuration.redis` component
briefly existed and was removed to keep components honest.

## Decision

I lean on Dapr's building blocks as the integration layer rather than
hand-rolling equivalents, using them uniformly across all four concerns:

1. Service invocation for sync calls (decision → config, approval →
   decision) via `DaprIO.invoke()` - no hardcoded peer URLs, correlation id
   propagated by the sidecar.
2. Pub/sub (`approvalflow-pubsub`, `pubsub.rabbitmq`) for all 8 async
   topics, with services declaring subscriptions via `GET /dapr/subscribe` and
   receiving CloudEvents.
3. State in one Redis-backed `statestore` with per-app `keyPrefix`
   (services cannot read each other's keys) and ETag first-write concurrency
   for payment's no-overspend guarantee.
4. Secrets via `secretstores.local.env` and `secretKeyRef`, so no
   credentials live in component yaml or code.
5. Workflow for the two durable flows (`decision_workflow` HITL pause,
   `payment_workflow` saga).

I deliberately **do not** use Dapr's *configuration* building block: thresholds
live in the state store and are served over service invocation by the config
service, which keeps a single runtime-mutation path (`PUT /config/thresholds`).
The unused `configuration.redis` component was removed to keep the component set
honest.

## Consequences

Positive. Uniform use of Dapr keeps the services thin and portable: no
service hardcodes a peer URL, no code embeds broker or store credentials, and
swapping the backing technology (RabbitMQ, Redis, the secret store) is a
component-yaml change rather than a code change. The building-block boundaries
also reinforce the architecture I want for free - `keyPrefix` enforces
per-service state ownership at the infrastructure layer, `secretKeyRef` keeps
secrets out of source, and correlation-id propagation through invocation gives
end-to-end tracing without bespoke plumbing. The durable Workflow block is what
makes the HITL pause (M11) and the payment saga (ADR-002) survive restarts
without a hand-written state machine.

Trade-offs. I take on operational weight: every service now runs beside
its own `daprd` sidecar, and the Workflow block forces `placement` and
`scheduler` containers into the compose file - more moving parts to start,
observe, and reason about than a few direct client libraries would need. I also
accept a framework dependency and its abstractions: behavior is shaped by
Dapr's semantics  rather than by code I fully control, which is a learning cost
and a coupling to Dapr's release cadence.

Risks and follow-ups. The sidecar is now in the critical path - a `daprd`
that is slow or down degrades invocation and pub/sub for its service, so sidecar
health is an operational concern I inherit. Using service invocation for
thresholds instead of the configuration building block means I own the
freshness story myself (see ADR-004's fail-safe); that was a conscious
simplification, but it is a place where a future maintainer might reasonably
reach for the block I chose not to use. Finally, `secretstores.local.env` is a
development-grade secret store - production would need a real backend, and the value of the abstraction is precisely that this is a
component swap, not a code change.
