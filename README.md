# ApprovalFlow - Invoice & Expense Approval Platform

Microservice-based, AI-assisted platform that automates invoice and expense approvals:
an AI agent judges each item against the company policy and **recommends**; a
**deterministic router decides**. The simple, low-risk majority is auto-approved under a
provable autonomy ceiling; unclear, risky, or high-value items are escalated to a human.
Approved items run through a compensating payment saga. Every decision is fully auditable
via one correlation id.

Capstone for the **Microservice Architecture** and **AI Engineering** courses.

## Application demo

[![ApprovalFlow application demo](docs/assets/application-demo.gif)](docs/assets/application-demo.mp4?raw=1)

[▶ Watch the full demo recording with audio (Google Drive)](https://drive.google.com/file/d/15M5egWcrvz94Md_I2aNZgjyzd7ovGngi/view?usp=sharing)

> Built in staged pull requests - see the PR history for the
> requirement-by-requirement trail. **All four worked journeys are green
> end-to-end** via `tools/verify.py` (11/11): auto-approve→paid, escalate-and-resume
> (surviving a decision-service restart), duplicate short-circuit, and payment
> failure + compensation - plus the anti-cheese guards, the gateway check,
> inbox delivery, and the auth (401/403) checks.

## Tech stack

- **Python 3.12 + FastAPI + Pydantic** - all services
- **Microsoft Agent Framework (Python)** - the decisioning agent (structured output only)
- **Dapr 1.18** - service invocation, pub/sub, state, config, secrets, **Workflow** (saga + durable HITL)
- **RabbitMQ** - message broker behind Dapr pub/sub
- **Redis** - per-service Dapr state stores (thresholds served by the config service, runtime-changeable)
- **nginx** - API gateway (single entry point, rate limiting) + serves the UI
- **React + Vite** - UI: form-based submission, live tracking with an audit
  timeline, approval queue, inbox, dashboard
- **Ollama + Qwen** (default) / Groq / Gemini / OpenAI / deterministic stub - LLM provider, swappable by config

## Run locally

```bash
cp .env.example .env   # adjust if desired
docker compose up
```

Everything - services, gateway, queue, databases, Dapr sidecars - comes up with that one
command. The UI is served at <http://localhost:8080>.

## Auth (N1)

User-facing API endpoints require a **self-signed role JWT** (roles:
`submitter` / `approver` / `admin`; admin passes every gate). The UI signs in
automatically via the role picker in the header; API clients trade a user+role
for a token first:

```bash
curl -s -X POST http://localhost:8080/api/auth/token \
  -H 'Content-Type: application/json' \
  -d '{"user":"dana.cohen@northwind.example","role":"submitter"}'
# → {"token":"...", ...}   then:  Authorization: Bearer <token>
```

Role map: submit needs `submitter`; the approval queue, decisions, budgets and
the dashboard need `approver`; the audit trail needs `admin`. Dapr-internal
surfaces (pub/sub handlers, service invocation, health checks) are not exposed
through the gateway and stay token-free. The issuer is a dev-grade stub
(HS256, secret in `AUTH_SECRET`) - production would swap in a real identity
provider without touching the route guards.

## Using the UI

The UI at <http://localhost:8080> has five tabs; the role picker in the header
signs you in (see [Auth](#auth-n1)):

- **Submit** - a form mirroring the invoice schema (line items, category,
  receipt flag, …) with one-click sample pre-fills; an **Edit as JSON** toggle
  posts a raw fixture as-is (handy for the `test.failPayment` demo hook).
  Accepted items return a tracking id and jump straight to Track.
- **Track** - live status and plain-language reason for one tracking id; with
  the `admin` role it also renders the full audit trail as a timeline
  (submitted → decision → human review → paid / failed).
- **Inbox** - final outcomes for a submitter email (M8).
- **Approvals** - the escalation queue (needs `approver`): each card shows the
  agent's recommendation, confidence, and cited policy rules; approve / reject /
  send back in one action (F5), with an optional note recorded to the audit
  trail. Decisions are attributed to the signed-in user.
- **Dashboard** - throughput and autonomy metrics (needs `approver`).

## Verify

A single command runs the four acceptance journeys (auto-approve, escalate-and-resume,
duplicate, payment failure + compensation) plus the anti-cheese, gateway, inbox, and
auth guards - 11 checks - and prints pass/fail:

```bash
python tools/verify.py    # uses the deterministic stub model - no rate limits
```

## Test

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

## CI / CD

CI runs the quality gates (ruff + pytest) and the end-to-end verification on
every push. On a green `main`, the CD stage automatically builds and publishes
all service images to GHCR as `ghcr.io/<owner>/approvalflow-<service>`,
tagged with the commit SHA - no manual release step (N2). The mutable `latest`
tags are promoted in a follow-up job only after **every** image published
successfully (and only if the commit is still the head of `main`), so `latest`
always describes one tested commit, never a mixed-version release.

## System diagrams

Canonical source: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) - the blocks
below are copied **verbatim** from there (edit there first, then re-copy, so
the two can never drift apart).

### Journeys 1 & 2 - submit → decide → (auto-approve | escalate-and-resume)

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

### Payment flow - saga with compensation (Journey 4, INV-1012)

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

## Repository layout

```
services/    FastAPI microservices (one folder per service) + shared library
gateway/     nginx gateway (UI + API entry point, rate limiting)
ui/          React + Vite frontend
dapr/        Dapr component definitions (pub/sub, state, secrets)
tools/       verification command + helpers
tests/       unit / integration tests
docs/        ARCHITECTURE.md, ADRs, product dilemma
Examples/    course-shipped fixtures: policy.md, sample-invoices.json
Documents/   assignment + course material
```

## License

[MIT](LICENSE)
