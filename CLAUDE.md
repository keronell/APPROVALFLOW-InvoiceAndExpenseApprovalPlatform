# CLAUDE.md — working guide for ApprovalFlow

Operational guidance for AI coding agents (and humans) working in this repo.
Keep it accurate; update it when conventions change.

## What this project is

ApprovalFlow is a microservice, AI-assisted invoice/expense approval platform
(capstone for Microservice Architecture + AI Engineering). An AI agent
**recommends**; a deterministic router **decides**. The autonomy ceiling must be
provably impossible to exceed (M12/F10). See `ASSIGNMENT.pdf` (in `Documents/`),
`Examples/policy.md`, and `Examples/sample-invoices.json` — these three are the
source of truth. Do not invent categories, thresholds, vendors, or expected
outcomes; derive them from those files.

## The one rule that must never break

The LLM never has final authority over money. The agent returns a structured
`AgentAssessment` only. The pure `route()` function
(`services/decision/approvalflow_decision/router.py`) makes the binding
decision, and the ceiling check reads the **policy engine's** USD amount, never
anything the model produced. Auto-approve is only ever reached in the final
branch where every condition already passed. Any change here must keep these
tests green: `tests/unit/test_router.py`, `tests/unit/test_fixtures_expected_routes.py`.

## Tech stack

- Python 3.12 · FastAPI · Pydantic v2
- Microsoft Agent Framework (`agent-framework`, **1.10**) — `Agent`,
  `AgentSession`, `@tool`, structured output via
  `ChatOptions(response_format=...)`. NOTE: the class names are the 2026 API
  (`Agent` not `ChatAgent`, `AgentSession` not `AgentThread`). Verify the
  installed surface before coding against it; the `agent/__init__.py` guard
  fails loudly if MAF drops an expected symbol.
- Dapr 1.18 — pub/sub (RabbitMQ), state + config (Redis), secrets, Workflow
- nginx gateway · React+Vite UI
- LLM provider is one config value (`LLM_PROVIDER`): `stub` | `ollama` | `groq`
  | `gemini` | `openai`. **CI and `tools/verify.py` always use `stub`** — a real
  `BaseChatClient` that runs the true MAF loop offline (no network, no limits).

## Repo layout

```
services/shared/     approvalflow_shared: contracts, correlation, logging, web app factory
services/decision/   approvalflow_decision: policy engine, router, MAF agent (agent/)
services/intake/     approvalflow_intake: async accept, dedupe, publish, status
services/<others>/   payment, approval, notification, config, reporting (added per stage)
gateway/  ui/  dapr/components/  tools/  tests/{unit,integration}/  docs/
```

Each service is its own installable package depending on `approvalflow-shared`.
Services **own their state** — no service reads another's store.

## Conventions

- **Wire vs code:** contracts use camelCase on the wire, snake_case in code
  (`_CamelModel` in `services/shared/approvalflow_shared/contracts.py`). Post
  fixtures as-is (`extra="ignore"`).
- **Correlation id = tracking id:** one id follows a request end-to-end (M14/F9),
  minted/propagated by `CorrelationIdMiddleware`, stamped on every structlog line.
- **Dependency inversion:** services depend on protocols (e.g. `SubmissionStore`,
  `EventPublisher`) with in-memory implementations for tests and Dapr-backed for
  production, chosen by env vars (`STORE_BACKEND`, `PUBLISHER_BACKEND`, etc.).
  Keep new external dependencies behind an abstraction so tests stay hermetic.
- **Fail loud, never silent (M15):** on provider/parse errors raise, don't
  default. See `DecisionAgent.assess`.
- **Prompt-injection defense:** the router never reads free text; the agent
  treats invoice notes as data, never instructions.

## Commands

```bash
# Python env (Windows: .\.venv\Scripts\python.exe)
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # installs each service editable

ruff check .            # lint gate
pytest                  # unit + integration (uses in-memory/stub backends)

docker compose up       # whole system (M4)
python tools/verify.py  # four journeys + anti-cheese guards (D5) — added in Stage 5+
```

When adding a service, add `-e services/<name>` to `requirements-dev.txt` so CI
installs it.

## Testing rules

- Every shipped fixture's `expected.route` is the regression floor — never weaken
  a test to make a change pass; fix the code or justify a threshold change in
  `docs/PRODUCT-DILEMMA.md` (and re-label).
- Tests must not hit the network or need real Dapr: use in-memory backends and
  `LLM_PROVIDER=stub`. Integration tests drive FastAPI via `TestClient`.
- Async tests use `@pytest.mark.anyio` (asyncio backend fixture in
  `tests/conftest.py`).

## Git workflow (this is graded — D3)

- GitHub Flow: one stage = one feature branch = one PR, merged to a green `main`.
- Small conventional commits (`feat:`, `test:`, `docs:`, `ci:`); commit tests
  with the code they cover. PR descriptions map to requirement IDs (F/M/N/B/D).
- Never commit secrets. `.env` is git-ignored; only `.env.example` is tracked.
- Commit messages via a file (`git commit -F <file>`) to avoid shell-quoting
  issues with multi-line bodies on Windows/PowerShell.

## Do NOT author (human-only — D2)

The ADR prose (`docs/adr/`) and the `docs/PRODUCT-DILEMMA.md` justification are
written and defended by a human. Create the stubs (headings, decision list,
enforced-numbers table) only — leave the reasoning prose blank.
