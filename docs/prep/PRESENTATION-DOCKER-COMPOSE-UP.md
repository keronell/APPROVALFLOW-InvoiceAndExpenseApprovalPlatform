# Presentation notes — "what happens when I run `docker compose up`"

Talking points for the live demo, pitched at "show I understand the system,"
not a deep-dive. Companion file for the invoice-flow walkthrough that follows
this one. Source: `docker-compose.yml` (19 containers), `.env.example`.

---

## The one-sentence version

One command boots eight independent microservices, each paired with its own
Dapr sidecar, plus the message broker, state store, local LLM, and the gateway
that fronts all of it — in an order the compose file enforces through health
checks, not luck.

---

## The four layers that come up, in order

**1. Infrastructure (no app code yet)**
- `rabbitmq` — the pub/sub broker every service's Dapr sidecar publishes/
  subscribes through (8 topics total: submitted, decided, payment.requested,
  payment.completed, etc.).
- `redis` — the state store: budgets, workflow state, idempotency keys,
  approval queue. Each service gets its own key prefix — nobody reads another
  service's data directly.
- `placement` + `scheduler` — Dapr's own control-plane containers (actor
  placement, Workflow scheduling). Nothing app-specific runs here.
- `ollama` — pulls and serves the local model (`qwen2.5:3b` by default) so the
  decision agent has a real LLM to call without hitting an external API.

Everything else `depends_on` Redis and RabbitMQ being **healthy** (not just
started) before it boots — that's what the `healthcheck:` blocks are for.

**2. Eight application services, each as a *pair* of containers**
`config`, `intake`, `decision`, `payment`, `approval`, `reporting`,
`notification` — each one is a FastAPI app container plus a `daprd` sidecar
container. The sidecar runs with `network_mode: service:<app>`, meaning it
shares the app's network namespace: the app always talks to its sidecar at
`localhost:3500`, and the sidecar always reaches the app at `localhost:8000`,
regardless of which service it is. Every sidecar is handed the same Dapr
building blocks (`--resources-path /components`: pub/sub, state store, secret
store) — that consistency is *why* adding a ninth service later is just
copy-pasting this pattern.

Each service also gets a host port bound to `127.0.0.1` only (8001–8007) —
reachable from the machine itself for debugging or `tools/verify.py`, but not
from outside. That's deliberate: nothing external is allowed to reach a
service directly.

**3. The gateway (port 8080) — comes up last**
`depends_on: intake, approval, reporting` all being started. It's the
**only** externally reachable entry point. It serves the React UI, proxies
API calls to the right service by path, rate-limits, and stamps a correlation
id on every request that doesn't already have one. Dapr-internal paths
(`/dapr/subscribe`, service invocation, `/healthz`) are deliberately **not**
proxied — so nothing can bypass the gateway's rate limiting by going straight
at a service, and nothing external can hit an endpoint that was only ever meant
for Dapr-to-Dapr traffic.

**4. Steady state**
Once healthy, the eight services are just sitting there subscribed to their
RabbitMQ topics, waiting for the first HTTP request to hit the gateway.
Nothing runs a background poll loop — it's entirely event-driven from here.

---

## Two things worth calling out if asked

- **Why per-service Dapr sidecars instead of one shared Dapr?** Isolation —
  each service's sidecar only knows about that service's app-id, and a crash
  or slow sidecar can't take another service down with it.
- **LLM_PROVIDER is one config value.** Compose defaults to `ollama` (a real
  local model, this container). CI and `tools/verify.py` instead set
  `LLM_PROVIDER=stub` — a deterministic fake client with no network calls —
  so the automated test suite never depends on a model actually being pulled
  and warmed up.

---

## If the demo needs to show it live

```bash
cp .env.example .env
docker compose up
```

Then hit `http://localhost:8080` for the UI, or `docker compose ps` to show
all 19 containers reporting healthy. `docker compose logs -f gateway` is a
good tail to have open — every request that lands shows its correlation id.
