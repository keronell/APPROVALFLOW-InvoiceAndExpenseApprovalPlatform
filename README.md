# ApprovalFlow — Invoice & Expense Approval Platform

Microservice-based, AI-assisted platform that automates invoice and expense approvals:
an AI agent judges each item against the company policy and **recommends**; a
**deterministic router decides**. The simple, low-risk majority is auto-approved under a
provable autonomy ceiling; unclear, risky, or high-value items are escalated to a human.
Approved items run through a compensating payment saga. Every decision is fully auditable
via one correlation id.

Capstone for the **Microservice Architecture** and **AI Engineering** courses.

> 🚧 Work in progress — built in staged pull requests. See the PR history for the
> requirement-by-requirement trail. Current: **all four worked journeys are green
> end-to-end** via `tools/verify.py` (9/9): auto-approve→paid, escalate-and-resume
> (surviving a decision-service restart), duplicate short-circuit, and payment
> failure + compensation — plus the anti-cheese guards and the gateway check.

## Tech stack

- **Python 3.12 + FastAPI + Pydantic** — all services
- **Microsoft Agent Framework (Python)** — the decisioning agent (structured output only)
- **Dapr 1.18** — service invocation, pub/sub, state, config, secrets, **Workflow** (saga + durable HITL)
- **RabbitMQ** — message broker behind Dapr pub/sub
- **Redis** — per-service state stores + Dapr configuration store
- **nginx** — API gateway (single entry point, rate limiting) + serves the UI
- **React + Vite** — minimal UI
- **Ollama + Qwen** (default) / Groq / Gemini / deterministic stub — LLM provider, swappable by config

## Run locally

```bash
cp .env.example .env   # adjust if desired
docker compose up
```

Everything — services, gateway, queue, databases, Dapr sidecars — comes up with that one
command. The UI is served at <http://localhost:8080>.

## Verify

A single command runs the four acceptance journeys (auto-approve, escalate-and-resume,
duplicate, payment failure + compensation) plus the anti-cheese guards, and prints
pass/fail:

```bash
python tools/verify.py    # uses the deterministic stub model — no rate limits
```

## Test

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

## System diagrams

Canonical source: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the blocks
below are copied **verbatim** from there (edit there first, then re-copy, so
the two can never drift apart).

### Journeys 1 & 2 — submit → decide → (auto-approve | escalate-and-resume)

<!-- diagram source of truth: docs/ARCHITECTURE.md — copied verbatim into README.md -->
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

    Submitter->>GW: POST /api/invoices
    Note over GW: nginx rate-limit (limit_req)<br/>mints/propagates X-Correlation-Id
    GW->>IN: proxy /api/invoices
    Note over IN: Dapr state store: fingerprint claim (fp::)<br/>+ idempotency key (idem::) — GLOBAL-DUP / M10
    IN-->>Submitter: 202 Accepted + trackingId
    IN--)DE: Dapr pub/sub (RabbitMQ): item.submitted
    Note over DE: Dapr Workflow: schedule decision_workflow<br/>(instance id = tracking id → redelivered events start nothing)
    DE->>CF: Dapr service invocation: GET config/thresholds
    CF-->>DE: thresholds ($250 ceiling, 0.80 confidence)
    Note over DE: if the invoke fails or returns nothing, DE falls back<br/>client-side to hardcoded defaults ($250/0.80) — config_client.py,<br/>logged as thresholds_fetch_failed, never crashes
    Note over DE: PolicyEngine (deterministic) + MAF agent (advisory)<br/>→ deterministic router decides (M12/F10)
    DE->>DE: Dapr state store: save decision::{trackingId} (audit, F9)
    DE--)IN: Dapr pub/sub: item.status-changed

    alt route = auto_approve — Journey 1 (INV-1001)
        DE--)PA: Dapr pub/sub: payment.requested
        Note over PA: payment saga — see payment-flow diagram
        PA--)IN: Dapr pub/sub: item.status-changed (paid)
        Submitter->>GW: GET /api/invoices/{trackingId}
        GW->>IN: proxy
        IN-->>Submitter: status paid + plain-language reason (F2)
    else route = human_review — Journey 2 (INV-1003)
        DE--)AP: Dapr pub/sub: escalation.requested<br/>(recommendation, confidence, cited rules — F4)
        Note over DE: Dapr Workflow: wait_for_external_event("HumanDecision")<br/>durable pause in the state store —<br/>survives a decision-service restart (M11)
        Approver->>GW: POST /api/approvals/{trackingId}/decision (one action — F5)
        GW->>AP: proxy
        AP->>DE: Dapr service invocation:<br/>POST api/decisions/{trackingId}/human-decision
        Note over DE: raise_workflow_event → workflow resumes<br/>exactly where it paused
        DE--)IN: Dapr pub/sub: item.status-changed (approved)
        DE--)PA: Dapr pub/sub: payment.requested
        PA--)IN: Dapr pub/sub: item.status-changed (paid)
    end

    Note over IN,PA: reporting subscribes to every topic<br/>(incl. escalation.resolved, published by approval) →<br/>dashboard metrics (F8) + audit trail per correlation id (F9)
```

### Payment flow — saga with compensation (Journey 4, INV-1012)

<!-- diagram source of truth: docs/ARCHITECTURE.md — copied verbatim into README.md -->
```mermaid
flowchart TD
    REQ["Dapr pub/sub: payment.requested consumed<br/>payment_workflow scheduled<br/>(instance id = tracking id — idempotent, M10)"]
    RES["1 reserve_activity → BudgetLedger.reserve()<br/>Dapr state store: budget::{department} decrement<br/>ETag first-write concurrency, retried on conflict (M9)"]
    PAY["2 pay_activity → provider.execute_payment()<br/>idempotent via payment::{trackingId}"]
    CON["3 confirm_activity → BudgetLedger.confirm()<br/>reservation → CONFIRMED (budget unchanged)"]
    DONE["4 complete_activity<br/>Dapr pub/sub: payment.completed<br/>+ item.status-changed (paid)"]
    REL["COMPENSATION<br/>release_activity → BudgetLedger.release()<br/>budget restored, reservation → RELEASED<br/>no orphaned reservation"]
    FAIL["fail_activity → _mark_failed()<br/>Dapr pub/sub: payment.failed<br/>+ item.status-changed (payment_failed)"]

    REQ --> RES
    RES -->|reserved| PAY
    RES -->|"insufficient budget<br/>(nothing reserved — no rollback needed)"| FAIL
    PAY -->|payment id| CON
    PAY -->|"PaymentDeclined<br/>(Journey 4: INV-1012 test.failPayment)"| REL
    REL --> FAIL
    CON --> DONE

    style REL fill:#7f1d1d,color:#fff
    style FAIL fill:#7f1d1d,color:#fff
    style DONE fill:#14532d,color:#fff
```

## Repository layout

```
services/    FastAPI microservices (one folder per service) + shared library
gateway/     nginx gateway (UI + API entry point, rate limiting)
ui/          React + Vite frontend
dapr/        Dapr component definitions (pub/sub, state, config)
tools/       verification command + helpers
tests/       unit / integration tests
docs/        ARCHITECTURE.md, ADRs, product dilemma
Examples/    course-shipped fixtures: policy.md, sample-invoices.json
Documents/   assignment + course material
```

## License

[MIT](LICENSE)
