# Code navigation cheatsheet

Pre-open these in IDE tabs in roughly this order before the session starts
(matches `07-checklist.md`). Grouped by talking point, not alphabetically.

## Entry point / gateway
| What | Path |
|---|---|
| Routing table, rate limit, correlation id | `gateway/nginx.conf` |
| Whole-stack boot order, all 19 containers | `docker-compose.yml` |

## Intake - the non-blocking front door
| What | Path |
|---|---|
| Submit endpoint, dedupe, publish | `services/intake/approvalflow_intake/app.py` |
| Two dedup namespaces (`fp::`, `idem::`) | `services/intake/approvalflow_intake/store.py:24` |

## Decision - the core of the talk
| What | Path |
|---|---|
| **The router** (read this one slowly) | `services/decision/approvalflow_decision/router.py:22` (`def route(`) |
| The deterministic policy engine | `services/decision/approvalflow_decision/policy_engine.py:52` (`def evaluate(`) |
| `Route` enum, `RouteDecision` model | `services/shared/approvalflow_shared/contracts.py:29`; `services/decision/approvalflow_decision/models.py:126` |
| Where policy + agent + router get unified | `services/decision/approvalflow_decision/service/decision_core.py:44` (`async def decide(`) |
| The agent itself (tools, prompt, structured output) | `services/decision/approvalflow_decision/agent/__init__.py`, `assessor.py`, `tools.py` |
| Config-with-fallback client | `services/decision/approvalflow_decision/service/config_client.py:20` (`async def fetch_thresholds(`) |
| The Dapr Workflow (pause/resume) | `services/decision/approvalflow_decision/service/workflow.py` |
| Router test proving forced-1.0-confidence still escalates | `tests/unit/test_router.py` (`test_forced_approve_over_ceiling_still_escalates`) |
| Fixture-replay regression floor | `tests/unit/test_fixtures_expected_routes.py` |

## Approval - the human queue
| What | Path |
|---|---|
| The one-click decide endpoint (claim → lease → side-effects) | `services/approval/approvalflow_approval/app.py:118` (`async def decide(`) |
| Concurrency/replay tests | `tests/integration/test_approval_api.py` |

## Payment - the saga
| What | Path |
|---|---|
| The orchestrator (reserve → pay → confirm → complete) | `services/payment/approvalflow_payment/workflow.py:39` |
| Budget ledger (reserve/release/confirm) | `services/payment/approvalflow_payment/budget.py:69` (`class BudgetLedger`) |
| ETag/CAS transaction (the "not a lock" bit) | `services/payment/approvalflow_payment/budget_dapr.py:28` (`commit_budget_and_reservation`) |
| Fake provider, idempotency key derivation | `services/payment/approvalflow_payment/provider.py` |
| Compensation test (budget restored on forced failure) | `tests/unit/test_budget_ledger.py` |

## Config, reporting, notification
| What | Path |
|---|---|
| Runtime-mutable thresholds, no redeploy | `services/config/approvalflow_config/app.py`, `defaults.py` |
| Cross-cutting audit trail + metrics | `services/reporting/approvalflow_reporting/app.py`, `service.py` |
| Per-submitter inbox, terminal-states-only | `services/notification/approvalflow_notification/app.py`, `service.py` |

## Cross-cutting (auth, correlation, contracts)
| What | Path |
|---|---|
| Role JWT guard | `services/shared/approvalflow_shared/auth.py:99` (`def require_role(`) |
| Correlation id middleware | `services/shared/approvalflow_shared/correlation.py:37` (`class CorrelationIdMiddleware`) |
| Wire contracts, camelCase↔snake_case | `services/shared/approvalflow_shared/contracts.py` |
| Shared FastAPI app factory | `services/shared/approvalflow_shared/web.py:13` (`def create_app(`) |

## Source-of-truth docs (open these, don't just cite them)
| What | Path |
|---|---|
| The autonomy rules being enforced | `Examples/policy.md` |
| The labeled fixtures (demo ids live here) | `Examples/sample-invoices.json` |
| Requirements-mapping + both sequence/flow diagrams | `docs/ARCHITECTURE.md` |
| The 4 ADRs | `docs/adr/adr-001-...md` through `adr-004-...md` |
| The dilemma writeup (why $250/0.80) | `docs/PRODUCT-DILEMMA.md` |
| End-to-end verification harness | `tools/verify.py` |

## UI (only if asked to show the frontend code, not just click it)
| What | Path |
|---|---|
| Tab shell | `ui/src/App.jsx` |
| Per-tab views (Submit/Track/Inbox/Approvals/Dashboard) | `ui/src/views/*.jsx` |
