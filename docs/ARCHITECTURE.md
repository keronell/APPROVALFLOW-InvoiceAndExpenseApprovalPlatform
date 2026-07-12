# ApprovalFlow - Architecture (D1)

This document holds the two canonical diagrams required by D1: the end-to-end
**sequence diagram** (Journey 1 auto-approve and Journey 2 escalate-and-resume,
branching at the router's decision) and the **payment-flow diagram** (Journey 4)
showing each saga step and its compensation.

> The same two Mermaid blocks are embedded verbatim in [README.md](../README.md)
> (marked with `diagram source of truth` comments). **Edit them here first, then
> copy the block verbatim into the README** so the two can never drift apart.

Every step below maps to real code - service names, topics, state keys, and
function names are the ones in the repo. Each arrow is labeled with the Dapr
building block it uses (service invocation, pub/sub over RabbitMQ, state store,
Workflow). The nginx gateway is the single external entry point (M6); one
correlation id - the tracking id - follows the request end-to-end (M14/F9).
User-facing calls carry a role JWT (N1) - submitting needs `submitter`, working
the queue needs `approver`, the audit trail needs `admin` - enforced by
`require_role` in each owning service; the gateway proxies only user-facing
routes, and Dapr-internal surfaces (pub/sub handlers, service invocation) are
never exposed through it.

## Journeys 1 & 2 - submit → decide → (auto-approve | escalate-and-resume)

<!-- diagram source of truth: docs/ARCHITECTURE.md - copied verbatim into README.md -->
```mermaid
sequenceDiagram
    autonumber
    actor Submitter
    actor Approver
    participant GW as Gateway (nginx :8080)
    participant IN as Intake
    participant DE as Decision (Dapr Workflow)
    participant CF as Config
    participant AP as Approval
    participant PA as Payment

    Submitter->>GW: POST /api/invoices<br/>(Bearer submitter JWT - N1)
    Note over GW: nginx rate-limit (limit_req)<br/>mints/propagates X-Correlation-Id
    GW->>IN: proxy /api/invoices
    Note over IN: Dapr state store: fingerprint claim (fp::)<br/>+ idempotency key (idem::) - GLOBAL-DUP / M10
    IN-->>Submitter: 202 Accepted + trackingId
    IN--)DE: Dapr pub/sub (RabbitMQ): item.submitted
    Note over DE: Dapr Workflow: schedule decision_workflow<br/>(instance id = tracking id → redelivered events start nothing)
    DE->>CF: Dapr service invocation: GET config/thresholds
    CF-->>DE: thresholds ($250 ceiling, 0.80 confidence)
    Note over DE: if the invoke fails or returns nothing, DE falls back<br/>client-side to hardcoded defaults ($250/0.80) - config_client.py,<br/>logged as thresholds_fetch_failed, never crashes
    Note over DE: PolicyEngine (deterministic) + MAF agent (advisory)<br/>→ deterministic router decides (M12/F10)
    DE->>DE: Dapr state store: save decision::{trackingId} (audit, F9)
    DE--)IN: Dapr pub/sub: item.status-changed

    alt route = auto_approve - Journey 1 (INV-1001)
        DE--)PA: Dapr pub/sub: payment.requested
        Note over PA: payment saga - see payment-flow diagram
        PA--)IN: Dapr pub/sub: item.status-changed (paid)
        Submitter->>GW: GET /api/invoices/{trackingId}
        GW->>IN: proxy
        IN-->>Submitter: status paid + plain-language reason (F2)
    else route = human_review - Journey 2 (INV-1003)
        DE--)AP: Dapr pub/sub: escalation.requested<br/>(recommendation, confidence, cited rules - F4)
        Note over DE: Dapr Workflow: wait_for_external_event("HumanDecision")<br/>durable pause in the state store -<br/>survives a decision-service restart (M11)
        Approver->>GW: POST /api/approvals/{trackingId}/decision<br/>(one action - F5, Bearer approver JWT)
        GW->>AP: proxy
        AP->>DE: Dapr service invocation:<br/>POST api/decisions/{trackingId}/human-decision
        Note over DE: raise_workflow_event → workflow resumes<br/>exactly where it paused
        DE--)IN: Dapr pub/sub: item.status-changed (approved)
        DE--)PA: Dapr pub/sub: payment.requested
        PA--)IN: Dapr pub/sub: item.status-changed (paid)
    end

    Note over IN,PA: reporting subscribes to every topic<br/>(incl. escalation.resolved, published by approval) →<br/>dashboard metrics (F8) + audit trail per correlation id (F9),<br/>notification files terminal outcomes into a per-submitter inbox (M8)
```

## Payment flow - saga with compensation (Journey 4, INV-1012)

Orchestrated by `payment_workflow` in
`services/payment/approvalflow_payment/workflow.py`; budget logic in
`budget.py` (`BudgetLedger`), provider in `provider.py`. Department budgets are
updated with ETag first-write concurrency so two concurrent approvals can never
overspend (INV-1014A/B). Every activity is idempotent (M10).

<!-- diagram source of truth: docs/ARCHITECTURE.md - copied verbatim into README.md -->
```mermaid
flowchart TD
    REQ["Dapr pub/sub: payment.requested consumed<br/>payment_workflow scheduled<br/>(instance id = tracking id - idempotent, M10)"]
    RES["1 reserve_activity → BudgetLedger.reserve()<br/>Dapr state store: budget::{department} decrement<br/>ETag first-write concurrency, retried on conflict (M9)"]
    PAY["2 pay_activity → provider.execute_payment()<br/>idempotent via payment::{trackingId}"]
    CON["3 confirm_activity → BudgetLedger.confirm()<br/>reservation → CONFIRMED (budget unchanged)"]
    DONE["4 complete_activity<br/>Dapr pub/sub: payment.completed<br/>+ item.status-changed (paid)"]
    REL["COMPENSATION<br/>release_activity → BudgetLedger.release()<br/>budget restored, reservation → RELEASED<br/>no orphaned reservation"]
    FAIL["fail_activity → _mark_failed()<br/>Dapr pub/sub: payment.failed<br/>+ item.status-changed (payment_failed)"]

    REQ --> RES
    RES -->|reserved| PAY
    RES -->|"insufficient budget<br/>(nothing reserved - no rollback needed)"| FAIL
    PAY -->|payment id| CON
    PAY -->|"PaymentDeclined<br/>(Journey 4: INV-1012 test.failPayment)"| REL
    REL --> FAIL
    CON --> DONE

    style REL fill:#7f1d1d,color:#fff
    style FAIL fill:#7f1d1d,color:#fff
    style DONE fill:#14532d,color:#fff
```

## Reading the diagrams against the requirements

| Requirement | Where it appears above |
|---|---|
| M6 single entry + rate limit | Gateway participant, first note |
| M8 async intake + notify | `202 + trackingId` before any processing; notification files terminal outcomes into the submitter's inbox (final note) |
| M9 compensation | red `release_activity` path - budget restored, then `payment_failed` |
| M10 idempotency | fingerprint/idempotency claims at intake; workflow instance id = tracking id; idempotent saga activities |
| M11 durable HITL | `wait_for_external_event` note - the pause survives a decision-service restart (proven by `tools/verify.py` Journey 2, which restarts the container between pause and resume) |
| M12/F10 provable ceiling | router note - the deterministic router decides; the agent is advisory |
| M14/F9 correlation | gateway mints/propagates `X-Correlation-Id` = tracking id; decision record + reporting audit trail keyed by it |
| F4/F5 | escalation payload carries recommendation/confidence/cited rules; one-action approve/reject/send-back |
| N1 role JWT auth | Bearer tokens on the submit/approve arrows; `require_role` guards live in each service, not the gateway - Dapr-internal surfaces stay unexposed |
