# Gateway (+ UI)

`gateway/nginx.conf`, `gateway/proxy_params.conf`, `gateway/Dockerfile` ·
external port `8080` · the **only** container in `docker-compose.yml` with no
matching `-dapr` sidecar — it sits entirely outside the Dapr mesh, a plain
HTTP reverse proxy in front of it.

## What it owns, and why it's a separate service

The gateway owns no application state at all — just routing rules, an
in-memory rate-limit bucket, and the compiled static UI bundle baked into its
image at build time. It's split out because M6 ("an API gateway must be the
single external entry point, with rate-limiting") is an infrastructure
concern that has no good home inside any one backend service: routing,
TLS-termination-in-a-real-deployment, and rate limiting shouldn't live in
`intake`'s code any more than in `payment`'s, and putting them in exactly one
place means no backend service ever has to know how to reach another one
through anything but its own Dapr sidecar.

nginx specifically (over, say, a hand-rolled FastAPI gateway) was chosen for
the "serve millions of users" framing the assignment invites: proven at
internet scale, `limit_req` built in, tiny footprint. It deliberately does
**not** verify JWTs — authorization stays inside each service's
`require_role` (see `09-cross-cutting.md`) — so the gateway itself stays
generic infrastructure with zero application awareness, rather than a second
place authorization logic could drift out of sync with the services.

## Inputs / outputs

The gateway's "endpoints" are `nginx.conf`'s `location` blocks — a routing
table, not application code:

| Prefix | Backend | Notes |
|---|---|---|
| `/api/auth`, `/api/invoices` | `intake:8000` | |
| `/api/approvals` | `approval:8000` | |
| `/api/budgets`, `/api/payments` | `payment:8000` | |
| `/api/reports`, `/api/audit` | `reporting:8000` | |
| `/api/notifications` | `notification:8000` | |
| `/config` | `config:8000` | |
| `/` (everything else) | static UI files | `try_files $uri /index.html` |
| `/healthz` | inline canned `200` | the gateway's *own* health, not a proxy |

**Deliberately not proxied: `/api/decisions`.** The comment in `nginx.conf`
says exactly why — it's a Dapr-internal surface (`approval` reaches it by
service invocation) that carries no role guard by design; exposing it through
the gateway would let an anonymous caller resolve someone else's paused
approval.

## Non-obvious design choices

**1. The correlation id can be minted at the very first hop, with no
application code involved.** `map $http_x_correlation_id $corr_id { "" 
$request_id; default $http_x_correlation_id; }` (`nginx.conf:18`) uses
nginx's own built-in `$request_id` as the fallback when the caller didn't
supply `X-Correlation-Id`. That means by the time *any* Python service sees a
request, it already has a correlation id attached — no service has to
remember to be "the one that mints it," and `intake`'s own middleware-level
minting (`correlation.py`) only ever kicks in for requests that reach a
service without going through the gateway at all (direct service-to-service
calls, or local testing).

**2. Rate limiting is per client IP, enforced before and independent of
authentication.** `limit_req_zone $binary_remote_addr zone=api:10m
rate=30r/s;` with `burst=60 nodelay` on every API location — nginx never
parses a JWT, so this is infrastructure-layer protection for the whole stack
(including the token issuer itself) rather than a per-account quota.

**3. The UI isn't a separate service — it's baked into this same image.**
`gateway/Dockerfile` is a two-stage build: stage one `npm run build`s the
React app, stage two copies the compiled `dist/` into the nginx image
alongside `nginx.conf`. There's no `ui` entry in `docker-compose.yml` at all
— what looks like "the frontend" from a browser is served by the exact same
container that proxies the API.

## The UI, briefly

`ui/src/` is a small React + Vite SPA — five tabs (Submit, Track, Inbox,
Approvals, Dashboard), each a thin fetch-and-render view over the same
gateway-proxied API a `curl` script would use. No UI-only backend logic
exists anywhere. Two things worth knowing if you're demoing it:

- The role picker in the header (`components.jsx`'s `AuthBar`) is a *real*
  sign-in — it calls `POST /api/auth/token` on every user/role change and
  stores the returned JWT in a module-level variable
  (`api.js`'s `setAuthToken`), attached as a `Bearer` header to every
  subsequent fetch. Switching the role dropdown genuinely changes what you're
  authorized to do, live.
- `SubmitView`'s "Edit as JSON" toggle exists specifically so a fixture —
  including the `test.failPayment` hook — can be pasted and submitted
  verbatim. That's how you'd manually walk through Journey 4 (payment
  failure + compensation) from the UI instead of via `tools/verify.py`.

## If this crashes right now

- **The gateway is the sole external entry point, so if it's down, the
  entire system is unreachable from outside** — no UI, no API, nothing. This
  is a real, accepted single point of failure in this docker-compose
  topology; a production fix would be a gateway replica set behind a load
  balancer, which this setup doesn't attempt.
- **Backend processing is completely unaffected.** An in-flight payment saga,
  a paused HITL workflow, pub/sub delivery between services — none of it
  routes through the gateway; Dapr sidecars talk to RabbitMQ, Redis, and each
  other directly. Work already in the pipeline keeps completing while the
  gateway is down; only *new* external interaction (submitting, checking
  status, approving, viewing the dashboard) is blocked until it's back.
- The gateway holds no state of its own — routing rules come from the config
  file baked into the image, rate-limit counters are in-memory — so
  restarting it is entirely clean: nothing to recover, no data to lose.
