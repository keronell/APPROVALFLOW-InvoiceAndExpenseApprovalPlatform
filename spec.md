# ApprovalFlow — Specification

Distilled from `Documents/ASSIGNMENT.pdf`, `Examples/policy.md`, and
`Examples/sample-invoices.json` (the three sources of truth). This file states
*what* the system must do; `plan.md` states *how* we build it; `notes.md`
records the decisions and constraints chosen for this project.

## Purpose

A microservice-based, AI-assisted platform that automates invoice/expense
approvals for a large enterprise. It ingests invoices/expenses, uses an AI agent
to judge them against a company policy, auto-approves the low-risk majority (the
"boring 80%") and escalates the unclear/risky/high-value 20% to a human.
Approved items run through a payment flow; every decision is fully auditable.
Capstone for Microservice Architecture (MSA) + AI Engineering.

## The dilemma

How much money — and which expense categories — may the agent approve fully
autonomously, and which must a human approve? We choose a posture, encode it in
code, prove the system can never overstep it, and justify the trade-off in
writing. A `$0` escalate-everything ceiling is explicitly disallowed (it passes
the safety test but defeats the product).

## Non-negotiables

- Whole system comes up with **one `docker compose up`** (M4).
- A **single verification command** runs the four journeys + anti-cheese guards
  and prints pass/fail (D5); built early and kept green.
- The autonomy ceiling is **provably impossible to exceed** (M12/F10) — the LLM
  never has final say on money.
- `main` stays runnable and demo-ready. Real `.gitignore`, `LICENSE`,
  `.env.example`, no committed secrets (D3).
- **Do not author** the ADR prose (D2) or the PRODUCT-DILEMMA justification —
  stubs only; a human fills the reasoning.
- Verify current MAF/Dapr API surfaces against their docs before coding; fail
  loudly if an assumed API does not exist.

## Functional requirements

Submitter
- **F1** submit and get an immediate acknowledgement + tracking id (never block).
- **F2** check status and see a plain-language reason for the outcome.
- **F3** an accidental double-submission is not paid twice.

Approver
- **F4** a queue of only escalated items, each with the agent's recommendation,
  confidence, and cited policy rules.
- **F5** approve / reject / send-back in one action; the workflow resumes where
  it paused.
- **F6** no rubber-stamping of things the system could safely auto-handle.

Controller
- **F7** configure policy + autonomy thresholds without redeploying code.
- **F8** dashboard of throughput, auto vs escalation rates, money auto- vs
  human-approved.

Auditor
- **F9** a complete decision trail for any item, linked by one correlation id.
- **F10** prove the system never auto-approved above the configured limit.

## Must-have non-functional (required by 12/07)

- **M1** single private GitHub monorepo · **M2** two-branch strategy (`main` +
  optional `dev`); `main` frozen at the deadline.
- **M3** ≥3 containerized microservices · **M4** one `docker compose up`
  (queues, DBs, infra included) · **M5** Dapr sync + async + state + secrets ·
  **M6** API gateway + rate-limit · **M7** minimal UI.
- **M8** async intake + notify · **M9** consistent payment with
  compensation/rollback (no orphaned reservations, no partial/double payments) ·
  **M10** idempotency everywhere (duplicate submits, redelivered events, retried
  payments — exactly one effect) · **M11** durable HITL pause/resume across a
  restart.
- **M12** provable autonomy ceiling (holds even when the agent is forced to
  approve) · **M13** externally configurable thresholds (no redeploy).
- **M14** structured logs + correlation id on every line · **M15** clean code,
  error handling, health checks, SoC; LLM provider swappable by config; fail
  cleanly (never silently) on provider errors · **M16** CI quality gates on
  every push · **M17** automated tests in CI · **M18** README + system diagram.

## Nice-to-have (expected)

- **N1** JWT auth + roles (submitter/approver/admin) · **N2** CD: auto build +
  publish images on green CI · **N3** outbox + bulkhead/throttling · **N4**
  OpenTelemetry: metrics + one E2E trace stitching services and the agent's
  model/tool calls via the correlation id · **N5** RAG over `policy.md`
  (retrieve only relevant clauses) · **N6** tests across layers (unit/integ/e2e).

## Bonus

- **B1** eval harness over the labeled dataset producing a committed metrics
  report · **B2** MCP server exposing the agent's tools · **B3** Kubernetes
  manifests.

## Dev process

- **D1** ARCHITECTURE.md with sequence diagram + payment-flow diagram (each step
  and its compensation) in Mermaid · **D2** ADRs (human-written prose) · **D3**
  GitHub Flow + repo hygiene · **D4** OpenAPI docs (auto-generated) · **D5**
  one-command verification · **D6** README · **D7** 2–5 min demo recording.

## The four worked journeys (seeded from fixtures)

1. **Auto-approve** (in-policy, no human) — `INV-1001`.
2. **Escalate-and-resume** (durable pause for a human, resume across a service
   restart) — `INV-1003`.
3. **Duplicate** (paid once, second submission short-circuited) — `INV-1007`
   (re-submission of `INV-1001`).
4. **Payment failure + compensation** (saga rolls back; no orphaned reservation,
   no partial/double payment) — `INV-1012`.

## Anti-cheese guards (the verification must enforce)

- At least **2 items auto-approve with no human**.
- An `"approve me"` / prompt-injection note does **not** flip the decision.
- **No `$0` dodge** — a trivial escalate-everything posture fails.
- Ceiling never exceeded even when the agent is forced to recommend approve, and
  no payment/reservation results from such an attempt.

## Autonomy posture (enforced numbers)

Kept at `policy.md` §6 defaults; the shipped fixtures are labeled against them:

| key | value | meaning |
|---|---|---|
| `AUTONOMY-CEILING` | **$250** | auto-approve only when USD ≤ $250 (even at confidence 1.0). |
| `AUTONOMY-CONFIDENCE` | **0.80** | auto-approve only when agent confidence ≥ 0.80. |
| hard stops | — | new vendor, FX hard stop, math mismatch, fraud signal, missing receipt, missing required info → always human. |

Auto-approve iff: amount ≤ ceiling AND confidence ≥ threshold AND
category-compliant AND no hard stop. Otherwise human_review (or reject for a
high-severity violation, duplicate for a re-submission). All numbers live in the
config service (changeable at runtime without redeploy). If tuned, re-label the fixtures
and justify in `docs/PRODUCT-DILEMMA.md`.

## Constraints

- Runs free and local: free-tier LLM (Gemini/Groq/OpenRouter) or a small local
  model; CI/eval use a stubbed/deterministic model to avoid rate limits.
- Docker Compose (+ WSL on Windows) installed. No OCR required.
