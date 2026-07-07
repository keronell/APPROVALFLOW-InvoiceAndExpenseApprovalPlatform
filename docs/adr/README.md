# Architecture Decision Records (D2)

Each key decision is captured as a short ADR: **context → decision →
consequences**. The CONTEXT sections below are pre-filled with facts from the
code as it exists. The **DECISION and CONSEQUENCES sections are deliberately
empty** — per the assignment (D2), the reasoning is written by the author, not
an AI agent, because these are the decisions defended live at the presentation.

| ADR | Decision | Status |
|---|---|---|
| [ADR-001](adr-001-policy-engine-router-split.md) | Deterministic policy engine + router, separated from the agent | context drafted — decision/consequences pending (author) |
| [ADR-002](adr-002-saga-over-2pc.md) | Payment saga with compensation vs. two-phase commit | context drafted — decision/consequences pending (author) |
| [ADR-003](adr-003-dapr-as-integration-layer.md) | Dapr as the integration layer vs. hand-rolled equivalents | context drafted — decision/consequences pending (author) |
| [ADR-004](adr-004-config-fallback-fail-safe.md) | Config client fails safe to policy.md defaults on outage | context drafted — decision/consequences pending (author) |

Further decisions worth an ADR if time allows: language/stack choice
(Python + MAF + FastAPI), RabbitMQ as the broker, nginx as the gateway,
the autonomy posture itself ($250 / 0.80 — see `docs/PRODUCT-DILEMMA.md`).
