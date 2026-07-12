# ApprovalFlow — Decisions, Constraints & Working Notes

The choices made for this project (mostly by the project owner during planning),
plus operating constraints and things worth remembering. `spec.md` = what;
`plan.md` = how; this file = *why we chose it this way*.

## Architecture & technology choices (owner decisions)

| Area | Choice | Rationale / notes |
|---|---|---|
| **Language / runtime** | **Python 3.12 + FastAPI + Pydantic** | Fast iteration; Pydantic gives typed contracts and validated structured output. Spec allowed Python + Dapr as an alternative to .NET. |
| **Agent framework** | **Microsoft Agent Framework (Python)** | Matches the AI course; `Agent` + typed `@tool` tools + structured output. The chat client is the only provider-specific line (M15). |
| **Distributed runtime** | **Dapr** (invocation, pub/sub, state, secrets, config, **Workflow**) | Dapr Workflow drives the payment saga and durable HITL pause/resume. |
| **API gateway** | **nginx** | Chosen for the "serve millions of users" narrative: proven at internet scale, built-in `limit_req` rate limiting, tiny footprint. JWT (N1) stays in the services. |
| **UI** | **React + Vite** | Small SPA (submit, status, approver queue, dashboard, inbox), built into the nginx container. Good for the demo recording. |
| **LLM provider (live)** | **Ollama + Qwen** (`qwen2.5:3b`) | Free and local. Ships as a compose container **by default**, with a **host-Ollama override** via one `.env` value (`LLM_BASE_URL` → `host.docker.internal:11434/v1`) for GPU speed. |
| **LLM provider (CI / verify)** | **Deterministic stub** | `LLM_PROVIDER=stub` — a real MAF `BaseChatClient` that runs the true agent loop offline. No network, no rate limits. Provider stays swappable to Groq/Gemini (all OpenAI-compatible). |
| **Message broker** | **RabbitMQ** | A real queue in compose — satisfies the MSA "queue" requirement explicitly; management UI helps the demo. |
| **State / config store** | **Redis** | Per-service Dapr state stores (per-app key prefix; no cross-service reads); thresholds live in the config service's state, served over service invocation. |
| **Autonomy posture** | **Keep `policy.md` §6 defaults: $250 ceiling, 0.80 confidence** | The shipped fixtures are labeled against these numbers. If tuned, re-label the fixtures and justify in `docs/PRODUCT-DILEMMA.md`. Numbers are served by the config service, runtime-changeable (F7/M13). |

## Hard constraints

- The **LLM never has final authority over money.** Agent recommends; the pure
  deterministic router decides; the ceiling check reads the policy engine's USD
  amount, never the model. Must hold even when the agent is forced to approve.
- **One `docker compose up`** brings up the whole system (M4).
- **Everything free and local** — no paid services.
- **No committed secrets.** `.env` is git-ignored; only `.env.example` tracked.
- **Verify current MAF/Dapr APIs before coding**; fail loudly if an assumed API
  is missing. (Confirmed: MAF **1.10** uses `Agent`/`AgentSession`/`@tool`, not
  the older `ChatAgent`/`AgentThread` names in the spec.)
- **Lean MAF dependency:** decision depends on `agent-framework-core` +
  `agent-framework-openai` (`>=1.10,<1.11`) — never the `agent-framework` meta
  package (its `[all]` extras made unconstrained pip resolves fail). Bump both
  pins deliberately and regenerate `services/decision/constraints.txt` inside
  the image, per that file's header.

## Working-process decisions (owner preferences)

- **Staged, professional git history is graded.** Build in stages: one feature
  branch + PR per stage, small conventional commits, tests committed with the
  code they cover, `main` always green. No big-bang drops. PR descriptions map
  to requirement IDs so reviewers can trace requirements → PRs.
- **Real PRs via GitHub Flow.** Use the GitHub CLI (`gh`) to open and merge PRs
  (needs one-time `gh auth login`). Stacked branches retarget to `main` cleanly.
- **Human-only writing (D2):** the ADR prose (`docs/adr/`) and the
  PRODUCT-DILEMMA justification are written and defended by the owner. Agents
  create stubs (headings, decision list, enforced-numbers table) only.
- **Planning was refined via Ultraplan** (cloud) and approved before
  implementation began.

## Conventions in force (see CLAUDE.md for detail)

- Contracts: camelCase on the wire, snake_case in code; fixtures post as-is.
- Correlation id = tracking id; one id end-to-end, on every log line (M14/F9).
- Dependency inversion: services depend on protocols (`SubmissionStore`,
  `EventPublisher`, …) with in-memory impls for tests and Dapr-backed impls for
  production, chosen by env vars — so tests stay hermetic.
- Fail loud, never silent, on provider/parse errors (M15).
- Prompt-injection defense: the router never reads free text; the agent treats
  invoice notes as data, not instructions.
- Commit multi-line messages via `git commit -F <file>` (PowerShell has no
  heredoc and mangles inline multi-line `-m`).

## Status snapshot (2026-07-11 — delivery)

- All stages plus the hardening rounds and N1/N2 are merged to `main` via
  PRs #1–#25; `main` is green (155 tests, ruff clean) and demo-ready.
- `tools/verify.py` passes **11/11** on a cold `docker compose up` with the
  stub model: J1 auto-approve→paid; J2 escalate → decision-service restart →
  approve → paid (durable HITL); J3 duplicate short-circuit; J4 payment
  failure + compensation (budget restored); ≥2 no-human auto-approvals;
  "approve me" note does not flip; INV-1014A/B concurrent approvals never
  overspend; no-$0 dodge; gateway serves UI + API + metrics; final outcome
  reaches the submitter's inbox (M8); anonymous 401 / wrong-role 403 (N1).
- Services live: intake (8001), decision (8002), config (8003), payment
  (8004), approval (8005), reporting (8006), notification (8007), each with
  its own daprd sidecar — all loopback-bound; the nginx gateway (8080) is the
  only public entry. RabbitMQ + Redis + placement + scheduler + ollama in
  compose.
- CD (N2): a green `main` publishes all eight images to GHCR
  (`ghcr.io/<owner>/approvalflow-<service>`) tagged with the commit SHA;
  `latest` is promoted only after every image lands.
- Remaining for the owner before the 2026-07-12 freeze: write the
  PRODUCT-DILEMMA Posture/Trade-off prose (D2). The ADR Decision/Consequences
  prose is written, the explainer drafts were dropped, and the demo recording
  ships in `docs/assets/` (D7). `main` freezes at the deadline.

## Source-of-truth files (do not contradict)

- `Documents/ASSIGNMENT.pdf` — requirements (F/M/N/B/D).
- `Examples/policy.md` — the enforced policy + thresholds (rule ids).
- `Examples/sample-invoices.json` — 20 labeled fixtures; grade on
  `expected.route`.
