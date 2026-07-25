# Flow file map — where each action actually happens

A companion to `13-flow-cheatsheet.md` (the mechanics) — this one is pure
lookup: for every action in the flow, the exact file and function it lives
in. Paths are relative to the repo root.

---

## Intake

| Action | File | Function / class |
|---|---|---|
| `POST /api/invoices` handler | `services/intake/approvalflow_intake/app.py` | `submit_invoice` |
| Pydantic schema validation | `services/shared/approvalflow_shared/contracts.py` | `InvoiceSubmission` |
| Correlation id resolution | `services/shared/approvalflow_shared/correlation.py` | `CorrelationIdMiddleware`, `get_correlation_id`, `new_correlation_id` |
| Business logic entry, `tracking_id = correlation_id` | `services/intake/approvalflow_intake/service.py` | `IntakeService.submit` |
| Idempotency-Key claim | `services/intake/approvalflow_intake/service.py` | `submit` (step 1) |
| Fingerprint property (`vendor\|invoiceNumber\|total`) | `services/shared/approvalflow_shared/contracts.py` | `InvoiceSubmission.fingerprint` |
| Fingerprint claim / duplicate branch | `services/intake/approvalflow_intake/service.py` | `submit` (step 2) |
| Atomic claim primitive (SETNX-style) | `services/intake/approvalflow_intake/dapr_adapters.py` | `DaprSubmissionStore._claim` |
| Persist `SubmissionRecord` (Redis write) | `services/intake/approvalflow_intake/service.py` | `submit` (step 3) |
| Publish `item.submitted` (RabbitMQ) | `services/intake/approvalflow_intake/service.py` | `submit` (step 3) |
| Publisher implementation | `services/intake/approvalflow_intake/dapr_adapters.py` | `DaprPublisher` |
| `GET /api/invoices/{id}` (status view) | `services/intake/approvalflow_intake/app.py` | `get_status` |
| Loop-back: consume `item.status-changed` | `services/intake/approvalflow_intake/app.py` | `on_status_changed` |
| Apply the status update | `services/intake/approvalflow_intake/service.py` | `apply_status_change` |

## Decision — policy engine, agent, router, workflow

| Action | File | Function / class |
|---|---|---|
| Consume `item.submitted`, schedule workflow | `services/decision/approvalflow_decision/service/app.py` | `on_item_submitted` |
| Idempotency check before scheduling | `services/decision/approvalflow_decision/service/app.py` | `on_item_submitted` (`get_workflow_state`) |
| Orchestration: `decide → publish → (pause) → resume` | `services/decision/approvalflow_decision/service/decision_core.py` | `decide` |
| Deterministic policy rules (all of policy.md §1–5) | `services/decision/approvalflow_decision/policy_engine.py` | `evaluate`, `_evaluate_category` |
| Fail-safe assessment (agent unreachable) | `services/decision/approvalflow_decision/service/decision_core.py` | `_fail_safe_assessment` |
| Agent orchestration (LLM call) | `services/decision/approvalflow_decision/agent/assessor.py` | `DecisionAgent.assess` |
| Agent tools (`fetch_policy`, `lookup_vendor`) | `services/decision/approvalflow_decision/agent/tools.py` | — |
| Provider selection (`stub`/`ollama`/`groq`/`gemini`/`openai`) | `services/decision/approvalflow_decision/agent/client.py` | `build_chat_client` |
| Offline stub agent (used by CI/`verify.py`) | `services/decision/approvalflow_decision/agent/stub_client.py` | `StubChatClient` |
| **The router** — the whole M12/F10 proof | `services/decision/approvalflow_decision/router.py` | `route` |
| `route → status` mapping | `services/decision/approvalflow_decision/models.py` | `ROUTE_TO_STATUS` |
| Dapr Workflow orchestrator | `services/decision/approvalflow_decision/service/workflow.py` | `decision_workflow` |
| Activity: run `decide()` | `services/decision/approvalflow_decision/service/workflow.py` | `decide_activity` |
| Activity: persist + publish outcome (`decision.made`, `item.status-changed`, `payment.requested`/`escalation.requested`) | `services/decision/approvalflow_decision/service/workflow.py` | `publish_outcome_activity` |
| Activity: apply the human's decision on resume | `services/decision/approvalflow_decision/service/workflow.py` | `apply_human_decision_activity` |
| Fetch thresholds (with fail-safe fallback) | `services/decision/approvalflow_decision/service/config_client.py` | `fetch_thresholds` |
| `GET /api/decisions/{id}` (audit read) | `services/decision/approvalflow_decision/service/app.py` | `get_decision` |
| `POST /api/decisions/{id}/human-decision` (resume) | `services/decision/approvalflow_decision/service/app.py` | `human_decision` |

## Config

| Action | File | Function / class |
|---|---|---|
| `GET /config/thresholds`, `PUT /config/thresholds` | `services/config/approvalflow_config/app.py` | `build_app` handlers |
| Hardcoded fallback thresholds | `services/config/approvalflow_config/defaults.py` | `DEFAULT_THRESHOLDS` |
| Threshold validation (bounds, hard cap) | `services/config/approvalflow_config/models.py` | `Thresholds`, `CEILING_HARD_CAP` |

## Payment — the saga

| Action | File | Function / class |
|---|---|---|
| Consume `payment.requested`, schedule workflow | `services/payment/approvalflow_payment/app.py` | `on_payment_requested` |
| Saga orchestrator (reserve → pay → confirm → complete) | `services/payment/approvalflow_payment/workflow.py` | `payment_workflow` |
| Reserve budget | `services/payment/approvalflow_payment/workflow.py` | `reserve_activity` / `_reserve` |
| Execute payment (idempotency-key-before-call pattern) | `services/payment/approvalflow_payment/workflow.py` | `pay_activity` / `_pay` |
| Compensation: release budget | `services/payment/approvalflow_payment/workflow.py` | `release_activity` / `_release` |
| Confirm / complete / fail | `services/payment/approvalflow_payment/workflow.py` | `confirm_activity`, `complete_activity`, `fail_activity` |
| Budget ledger (ETag first-write concurrency) | `services/payment/approvalflow_payment/budget.py` | `BudgetLedger` |
| Budget state store adapter | `services/payment/approvalflow_payment/budget_dapr.py` | `DaprBudgetStore` |
| Fake payment gateway | `services/payment/approvalflow_payment/provider.py` | `execute_payment` |
| `GET /api/budgets/{department}`, `GET /api/payments/{id}` | `services/payment/approvalflow_payment/app.py` | `get_budget`, `get_payment` |

## Approval — the escalation queue

| Action | File | Function / class |
|---|---|---|
| Consume `escalation.requested`, enqueue | `services/approval/approvalflow_approval/app.py` | `on_escalation` |
| `GET /api/approvals` (the queue) | `services/approval/approvalflow_approval/app.py` | `list_queue` |
| `POST /api/approvals/{id}/decision` — claim → lease → side effects → mark done | `services/approval/approvalflow_approval/app.py` | `decide` |
| Side effect: resume the workflow | `services/approval/approvalflow_approval/app.py` | `DaprDecisionGateway.raise_human_decision` |
| Side effect: publish `escalation.resolved` | `services/approval/approvalflow_approval/app.py` | `DaprDecisionGateway.publish_resolved` |
| Claim / lease / mark-done primitives | `services/approval/approvalflow_approval/store.py` | `DaprQueueStore` (`try_resolve`, `try_begin_side_effects`, `mark_side_effects_completed`) |

## Notification & Reporting (pure sinks)

| Action | File | Function / class |
|---|---|---|
| Consume `item.submitted` (record who to notify later) | `services/notification/approvalflow_notification/app.py` | `on_item_submitted` |
| Consume `item.status-changed`, deliver if terminal | `services/notification/approvalflow_notification/app.py` | `on_status_changed` |
| Terminal-status allowlist + inbox dedupe | `services/notification/approvalflow_notification/service.py` | `TERMINAL_STATUSES`, `InboxStore.append` |
| `GET /api/notifications` | `services/notification/approvalflow_notification/app.py` | handler |
| Consume all 6 tracked topics | `services/reporting/approvalflow_reporting/app.py` | `build_app` subscriptions |
| Metrics + audit recording (dedupe-in-same-write) | `services/reporting/approvalflow_reporting/service.py` | `record`, `update_metrics`, `append_audit` |
| `GET /api/reports/summary`, `GET /api/audit/{id}` | `services/reporting/approvalflow_reporting/app.py` | handlers |

## Gateway & shared kernel

| Action | File | Function / class |
|---|---|---|
| Routing table, rate limiting, correlation-id fallback | `gateway/nginx.conf` | `location` blocks, `limit_req_zone`, `map $corr_id` |
| Header forwarding to backends | `gateway/proxy_params.conf` | `proxy_set_header X-Correlation-Id` |
| All event/contract shapes (camelCase ↔ snake_case) | `services/shared/approvalflow_shared/contracts.py` | `_CamelModel` + every event class |
| Correlation id plumbing | `services/shared/approvalflow_shared/correlation.py` | `CorrelationIdMiddleware` |
| Dapr HTTP client (state/invoke/pub-sub) | `services/shared/approvalflow_shared/daprio.py` | `DaprIO` |
| Role-based auth guard | `services/shared/approvalflow_shared/auth.py` | `require_role` |
| Shared FastAPI app factory + logging setup | `services/shared/approvalflow_shared/web.py`, `logging.py` | `create_app`, `configure_logging` |
