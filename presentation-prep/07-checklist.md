# Pre-presentation checklist

## Branch

No separate "dev" or "bonus-features" branch exists - every other branch is
a completed, already-merged PR, fully reachable from `main`. The current
branch, `prep`, is ahead of `main` by documentation only (`docs/learn/*.md`,
`docs/prep/*.md`, this `presentation-prep/` folder) - application code is
identical between the two branches.

Present from `prep`: the format calls for navigating markdown files live,
and all of them only exist on this branch.
- [ ] Confirm: staying on `prep` for Monday.

## Environment / real LLM

- [x] `.env` has `LLM_PROVIDER=ollama` and `OLLAMA_MODEL=qwen2.5:3b` - the
      demo hits a real local model, not a mock, by default.
- [x] All 8 service images + gateway are already built locally, and an
      `ollama` volume already exists from a prior run.
- [ ] Verify the model is actually cached in that volume: `docker compose
      up -d ollama` then `docker compose exec ollama ollama list` - confirm
      `qwen2.5:3b` shows up with no download happening. A cold pull live
      would cost several minutes.
- [ ] Run a full `docker compose up` end-to-end once before Monday and
      submit one fixture through the real UI to confirm the whole path -
      auth token issue, submit, route, pay - works against the live model.
- [ ] Double-check `AUTH_SECRET` / `CONFIG_API_TOKEN` in `.env` are set to
      something (dev defaults are fine) - an unset secret would 500 rather
      than silently no-op.

## App / demo mechanics

- [ ] `docker compose up -d` run before the session starts, not during it -
      confirm `docker compose ps` shows all ~19 containers healthy with
      time to spare.
- [ ] UI reachable at `http://localhost:8080` on the presentation machine
      specifically.
- [ ] Demo fixtures known cold: `INV-1001` (auto-approve), `INV-1003`
      (escalation), `INV-1012` (payment-failure compensation).
- [ ] Full script practiced once, timed - 15 minutes presenting is tight;
      know what to cut first if running long (Demo 3 first, the
      fraud/injection fixtures second).

## IDE panes - pre-open in this order

- [ ] `docs/ARCHITECTURE.md` (both diagrams, as a fallback if the mermaid
      diagram in `02-architecture-walkthrough.md` doesn't render)
- [ ] `services/decision/approvalflow_decision/router.py`
- [ ] `services/decision/approvalflow_decision/policy_engine.py`
- [ ] `services/payment/approvalflow_payment/workflow.py`
- [ ] `services/payment/approvalflow_payment/budget_dapr.py`
- [ ] `services/approval/approvalflow_approval/app.py`
- [ ] `gateway/nginx.conf`
- [ ] `docker-compose.yml`
- [ ] A browser tab on the UI, logged in as each role needed (submitter,
      approver, admin), or the token endpoint handy to mint them live.

## Logistics

- [ ] Laptop charged, charger packed regardless.
- [ ] Docker Desktop (or equivalent) actually running before starting, not
      just installed.
- [ ] Network situation known - nothing here needs external access mid-demo
      (`ollama`/RabbitMQ/Redis are all local containers).
- [ ] README's demo GIF/video link handy as an offline fallback if live
      Docker fails entirely on presentation hardware.
- [ ] `presentation-prep/` files committed somewhere reachable if the IDE
      session resets.
