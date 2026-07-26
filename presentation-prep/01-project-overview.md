# Project overview - elevator pitch

## The pitch (~30 seconds)

ApprovalFlow is an invoice and expense approval platform, built as a
capstone spanning a microservice-architecture course and an AI-engineering
course. It solves a specific, real problem: a company wants an AI agent to
triage expense submissions - reading receipts, notes, vendor history - but
can never let that agent be the thing that actually approves money moving.
So the system splits the job in two: an AI agent (Microsoft Agent Framework,
running a local Qwen model via Ollama by default, swappable to Groq/Gemini/
OpenAI or a deterministic stub) *recommends* a confidence score and a
category read; a small, pure, fully-tested `route()` function *decides* -
and that function is structurally incapable of reading the agent's
recommendation to approve anything, only its confidence, and only to
downgrade. Everything else - eight FastAPI microservices (intake, decision,
payment, approval, config, reporting, notification, plus an nginx gateway),
each with its own Dapr sidecar for pub/sub (RabbitMQ), state (Redis), and
workflow orchestration - exists to make that one guarantee operational: async
submission that never blocks, a payment saga that never overspends or
double-pays, and a durable human-in-the-loop pause that survives a container
restart.

## Why these choices, in one line each

- **Python 3.12 + FastAPI + Pydantic v2** - fast to write, strong typing at
  every service boundary, and the ecosystem the AI-engineering half of the
  course is taught in.
- **Microsoft Agent Framework** (`agent-framework-core` + `-openai`,
  pinned `>=1.10,<1.11`) - structured-output tool-calling agent framework;
  pinned to the core+openai packages specifically to dodge the `[all]`
  meta-package's unresolvable extras.
- **Swappable LLM provider (`LLM_PROVIDER` env var: `ollama` default, or
  `groq`/`gemini`/`openai`/`stub`)** - the AI half of the grade is about the
  agent pattern, not lock-in to one vendor; `stub` gives CI a real,
  deterministic, offline `BaseChatClient` so the test suite never depends on
  a live model.
- **Dapr 1.18** - one integration layer for pub/sub, state, service
  invocation, and Workflow, instead of hand-rolling a RabbitMQ client, a
  Redis client, and a saga engine separately in eight services.
- **nginx gateway** - the single external entry point (rate limiting,
  correlation-id stamping), proven at real-world scale, so no backend service
  has to reason about being reachable from outside the cluster.
- **React + Vite UI** - a thin, fast client for the four-role demo flow
  (submit -> track -> approve -> report); the interesting engineering work is
  behind the API, not in the frontend.
- **Eight services, not one and not eighteen** - each boundary maps to a
  named requirement or a genuinely different consistency problem (full
  reasoning in `03-key-decisions.md`).

## The core idea

*The interesting engineering problem isn't "call an LLM" - it's proving,
structurally, that the LLM's output can never be the thing that moves money,
while still getting real value from what it's good at (reading messy
invoice text, flagging fraud patterns, writing a plain-language reason).*
