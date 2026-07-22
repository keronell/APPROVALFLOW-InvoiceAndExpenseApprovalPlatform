# Config

Package: `services/config/approvalflow_config/` · App id: `config` · Port
`8003` (loopback) · endpoints proxied at `/config`.

## What it owns, and why it's a separate service

Config owns exactly one document: the current autonomy thresholds (one key,
`thresholds`, in its state store), plus the hardcoded fallback values in
`defaults.py` that seed it until a controller ever overrides anything.

It exists as its own service because F7/M13 are fundamentally a **runtime
mutation** concern — "change the policy without a redeploy" — which is a
different job from *enforcing* the policy (decision's job). Splitting it out
means the router and policy engine never need to know *how* a threshold got
set — by a human via `PUT`, or in principle by the automated proposal loop
sketched in `docs/FUTURE-WORK.md` idea 1 — they just ask for current values.
Centralizing it also means the mutation endpoint is one single, audited,
single-token-gated surface, rather than N services each owning their own
copy of "what's the ceiling right now."

Worth knowing for the "why Dapr this way" conversation: ADR-003 mentions
Dapr's own *configuration* building block was deliberately **not** used here
— thresholds live in plain state, served over service invocation, precisely
so there's exactly one mutation path (`PUT /config/thresholds`) rather than
two overlapping ones.

## Inputs / outputs

**Endpoints** (`app.py`) — config has **no `/dapr/subscribe` route at all**:
it's the only backend service with zero pub/sub involvement, purely
request/response.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /config/thresholds` | **none** | Current thresholds, or `DEFAULT_THRESHOLDS` if nothing's been stored |
| `PUT /config/thresholds` | `X-Config-Token` header (not a JWT role) | Validated runtime override (F7/M13) |
| `GET /config/policy` | **none** | A hardcoded human-readable policy summary |

**Publishes / subscribes:** nothing. **State it owns:** `thresholds` — a
single document, the smallest state footprint of any service here.

## Non-obvious design choices

**1. `GET` is fully open; `PUT` uses a completely different auth mechanism
from every other write in the system.** Every other mutating endpoint in
ApprovalFlow is gated by `require_role(...)` (a JWT role); this one is gated
by a shared-secret header (`X-Config-Token`, checked against
`CONFIG_API_TOKEN`, `app.py:51`) instead. That means an `admin` JWT — which
passes every other gate in the system — grants **zero** authority to change
the autonomy ceiling. Only whoever holds the separate config token can.
That's a deliberately narrower, stronger gate than the role system (a JWT is
mintable by anyone hitting the open `/api/auth/token` issuer; the config
token is a real secret from `.env`), but it *is* inconsistent with the rest
of the system's auth story, and worth having a direct answer ready for if
that inconsistency gets asked about.

**2. A `PUT` is a merge, not a replacement, and validation runs on the whole
merged document, never on the raw patch alone.** `put_thresholds`
(`app.py:39`) computes `merged = {**DEFAULT_THRESHOLDS, **stored, **patch}`
— you can `PUT {"ceiling_usd": "500"}` and every other field keeps its
current-or-default value — then validates the *entire* merged document
against `Thresholds` (`models.py`, `extra="forbid"`, every field bounded)
before persisting anything. A partial update can never leave the stored
document in a state where some fields are validated and others aren't.

**3. There's a hard ceiling on the ceiling, in code, above the runtime-mutable
one.** `CEILING_HARD_CAP = Decimal("10000")` (`models.py:17`) bounds
`ceiling_usd` in the `Thresholds` model itself — the file's own comment says
raising it is *"a deliberate code change, not a config PUT."* Even someone
holding a valid `CONFIG_API_TOKEN` cannot push the autonomy ceiling past
$10,000 through the API; that requires an actual code change and redeploy.
`test_invalid_values_are_rejected` covers this alongside negative/zero
category caps, confidence outside `[0,1]`, and zero/negative FX rates.

**4. The defaults are duplicated on purpose, in two places that must stay in
sync.** `services/config/approvalflow_config/defaults.py`'s
`DEFAULT_THRESHOLDS` and `services/decision/approvalflow_decision/models.py`'s
`AutonomyThresholds` field defaults encode the *same* numbers
independently. That's ADR-004's fallback story: decision's safety net can't
depend on config being reachable to know what "safe" means, so it carries its
own copy. ADR-004 names the cost plainly: a `policy.md` §6 change has to land
in both places or they silently disagree.

## If this crashes right now

- Config has almost no in-flight state to corrupt — it's one document behind
  one `save_state` call, which is atomic at the HTTP-call granularity. A
  crash mid-`PUT` either lands the whole validated document or leaves the
  previous one untouched; there's no partially-written threshold document.
- **The important fact:** if `config` is down entirely, `GET /config/thresholds`
  fails for a direct caller, but `decision`'s `fetch_thresholds` (see
  `02-decision.md` §4 and ADR-004) catches that and falls back to the
  hardcoded safe defaults. **A config outage degrades decision's precision,
  never its safety or its availability** — decisions keep being made, just
  against the conservative shipped posture, never blocked or crashed.
- Any previously-stored override is not lost on a config restart — it's
  Redis-backed and durable — but it *is* unreachable while config is down.
  If a controller had tightened the ceiling at runtime (say to $100), an
  outage in that window means `decision` uses the looser $250 default until
  config comes back — exactly the semantic gap ADR-004 names explicitly as
  "safe relative to the shipped posture, not relative to a stricter operator
  override."
