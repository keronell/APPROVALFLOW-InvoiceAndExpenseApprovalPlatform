# ApprovalFlow — Implementation Plan

*How* we build what `spec.md` describes, using the decisions in `notes.md`.
This mirrors the approved plan and the staged delivery in progress.

## Architecture (containers via one `docker compose up`)

```
Browser → nginx gateway (:8080, rate-limit, serves React UI)
   │  /api/* proxied to services (via their Dapr sidecars)
   ▼
intake ──item.submitted──▶ decision (Dapr Workflow orchestrator)
                              │ agent (MAF) + deterministic PolicyEngine + Router
                              ├─ escalation.requested ──▶ approval (HITL queue)
                              │      ◀── raise_event(HumanDecision) ──┘
                              ├─ payment.requested ──▶ payment (saga workflow)
                              │                          reserve→pay→confirm / compensate
                              └─ decision.made / status events ──▶ notification, reporting, intake
```

| Service | Responsibility | Owned state |
|---|---|---|
| **gateway** (nginx) | single entry, rate limit, serve UI, inject `X-Correlation-Id` | — |
| **intake** | 202 + tracking id, fingerprint dedupe (GLOBAL-DUP), publish `item.submitted`, status endpoint with plain-language reason (F1/F2/F3/M8/M10) | submissions + fingerprints (Redis) |
| **decision** | per-item Dapr Workflow: PolicyEngine → MAF agent → Router → escalate/reject/auto-approve → hand to payment → publish events; enforces the ceiling (M12/F10/F4/F6) | workflow state, decision records |
| **approval** | escalation queue with recommendation/confidence/cited rules; approve/reject/send-back in one action → raises workflow event; durable pause/resume (F4/F5/M11) | queue items (Redis) |
| **payment** | saga: ReserveBudget → ExecutePayment → ConfirmPayment + ReleaseBudget compensation; per-department budget with ETag concurrency; idempotent (M9/M10) | budgets, reservations, payments (Redis) |
| **notification** | subscribes final-outcome events; per-submitter inbox (M8) | notifications (Redis) |
| **config** | serves/updates policy + thresholds via the Dapr config store; read fresh per item → change without redeploy (F7/M13) | policy + thresholds (Redis config store) |
| **reporting** | subscribes all events; metrics + audit trail per correlation id (F8/F9) | aggregates + audit log (Redis) |

Infra: `redis`, `rabbitmq` (+mgmt UI), Dapr `placement` + `scheduler`, one Dapr
sidecar per service, `ollama` (default; `.env` can point at host Ollama).

**Events:** `item.submitted`, `decision.made`, `escalation.requested`,
`escalation.resolved`, `payment.requested`, `payment.completed`,
`payment.failed`, `item.status-changed`.

**Correlation id (M14/F9):** the tracking id doubles as the correlation id;
minted at the gateway/intake, propagated on HTTP headers and in event payloads,
stamped on every structured log line.

## The ceiling — classifier proposes, router decides (M12/F10)

1. **Deterministic PolicyEngine** (pure Python, no LLM): FX→USD, math
   reconciliation, receipt/vendor hard stops, category caps
   (MEAL/SAAS/HW/TRAVEL), alcohol reject, combined fraud heuristic. Emits typed
   findings + a USD amount.
2. **MAF Agent** with tools `fetch_policy` + `lookup_vendor`, returns a
   structured `AgentAssessment` only — advisory, no authority. Notes are data,
   never instructions.
3. **Router** — pure `route(evaluation, assessment, thresholds)`: auto-approve
   only in the final branch where every condition already passed (USD ≤ ceiling
   ∧ confidence ≥ threshold ∧ category-compliant ∧ no hard stop ∧ no rejection).
   The ceiling check reads the engine's USD, never the agent; confidence can
   only downgrade.
4. **Proof:** forcing the agent to approve an over-ceiling item still escalates
   and reserves no budget.

**Stub LLM** (`LLM_PROVIDER=stub`): a real `BaseChatClient` running the true MAF
loop offline, used by CI and `tools/verify.py`.

## Repo layout

```
services/{intake,decision,approval,payment,notification,config,reporting}/  # FastAPI + Dockerfile each
services/shared/     approvalflow_shared (contracts, correlation, logging, app factory)
gateway/  ui/        nginx + React/Vite
dapr/components/     pubsub (rabbitmq), statestore + configstore (redis), secretstore (env)
tools/verify.py      single verification command (D5)
tests/{unit,integration}/
docs/ARCHITECTURE.md · docs/adr/ (stubs) · docs/PRODUCT-DILEMMA.md (stub)
docker-compose.yml · .env.example · .gitignore · LICENSE · README.md · CLAUDE.md
```

## Staged delivery & git discipline (graded — D3)

One stage = one feature branch = one PR, merged to a green `main`. Small
conventional commits; tests committed with the code they cover. A stage merges
only when its tests pass, `verify.py` passes for journeys implemented so far,
and CI is green. PR descriptions map to requirement IDs.

### Stages and their test gates

1. **`chore/scaffold`** — hygiene, compose infra, shared lib, CI. ✅ done
2. **`feat/policy-engine-router`** — engine + router; 20-fixture route suite +
   forced-approve proof. ✅ done
3. **`feat/intake`** — 202/dedupe/publish/status; unit+integration. ✅ done
4. **`feat/agent`** — MAF agent, stub client, provider swap, fail-fast;
   agent→router reproduces fixtures. ✅ done
5. **`feat/decision-workflow`** — Dapr Workflow + config service + notification;
   **J1 green in verify.py**. ⏳ next
6. **`feat/payment-saga`** — reserve/pay/confirm + compensation, budget
   concurrency; **J4 green**, no-overspend test.
7. **`feat/duplicate-guard`** — end-to-end duplicate; **J3 green**.
8. **`feat/hitl-approval`** — queue + `raise_event` + durable resume; **J2 green
   incl. restart**; full verify.py (4 journeys + guards) in CI.
9. **`feat/ui-dashboard`** — React UI + reporting + OpenAPI via gateway.
10. **`docs/architecture`** — README, ARCHITECTURE.md diagrams, ADR +
    PRODUCT-DILEMMA stubs.
11. **Nice-to-haves**, each its own branch, priority order: N1 → N5 → N6 → N4 →
    N3 → N2 → B1 → B2 → B3. `main` stays demo-ready.

## Verification (definition of done)

- `docker compose up` → all health checks green, UI at `:8080`.
- `python tools/verify.py` (stub model) → four journeys + anti-cheese guards
  PASS with a clear pass/fail and exit code.
- Same command in CI on every push.
- Every fixture's `expected.route` reproduced by the engine+router suite (the
  regression floor).
- Ollama path: submit `INV-1001` via UI → live Qwen decision with cited rules.
