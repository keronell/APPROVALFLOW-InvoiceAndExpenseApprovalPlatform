# ADR-003 — Dapr as the integration layer vs. hand-rolled equivalents

- Status: context drafted — decision/consequences pending (author)
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
  `DaprIO.invoke()` — no hardcoded peer URLs, correlation id propagated.
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
  resolved via `secretKeyRef` in `dapr/components/pubsub.yaml` — no
  credentials in component yaml or code.
- **Workflow:** `decision_workflow` (durable HITL pause via
  `wait_for_external_event`, survives a service restart — M11) and
  `payment_workflow` (the saga of ADR-002). This building block is the reason
  the compose file runs `placement` and `scheduler`.

One deliberate simplification: Dapr's *configuration* building block is not
used. Thresholds live in the state store and are served over service
invocation by the config service (runtime-changeable via
`PUT /config/thresholds`, F7/M13). An unused `configuration.redis` component
briefly existed and was removed to keep components honest.

## Decision

<!-- TODO: write this yourself — you defend this live -->

## Consequences

<!-- TODO: write this yourself — you defend this live -->
