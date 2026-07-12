# ADR-004 - Config client fails safe to policy.md defaults on outage

- Status: context drafted - decision/consequences pending (author)
- Date: 2026-07-07

## Context

Autonomy thresholds must be changeable at runtime without a redeploy (F7/M13):
the config service serves them from the Dapr state store
(`PUT /config/thresholds` overrides at runtime; defaults in
`services/config/approvalflow_config/defaults.py` mirror `policy.md` §6 -
$250 ceiling, 0.80 confidence). The decision service fetches thresholds **per
item** via Dapr service invocation, which introduces a failure mode: what
should a money-deciding pipeline do when its policy source is unreachable?

The actual fail-safe logic,
`services/decision/approvalflow_decision/service/config_client.py:20-31`:

```python
async def fetch_thresholds(
    dapr: DaprIO, *, correlation_id: str | None = None
) -> AutonomyThresholds:
    try:
        data = await dapr.invoke(
            CONFIG_APP_ID, "config/thresholds", verb="GET", correlation_id=correlation_id
        )
        if data:
            return AutonomyThresholds.model_validate(data)
    except Exception as exc:  # noqa: BLE001 - resilience: fall back to safe defaults
        log.warning("thresholds_fetch_failed", error=str(exc))
    return AutonomyThresholds()
```

Behavioral facts:

- The fallback is entirely **client-side in the decision service** - the config
  service has no fallback logic of its own. Any invoke exception, validation
  error, or empty response yields the hardcoded `AutonomyThresholds()` defaults
  ($250 / 0.80, policy.md §6), with one `thresholds_fetch_failed` warning.
  The pipeline never crashes and never silently loosens.
- Module docstring (lines 3–5) states the intent: *"the safe posture is never
  looser than the defaults, so a config outage cannot widen the autonomy
  ceiling."*
- Honest caveat: if a controller had **tightened** thresholds at runtime (e.g.
  lowered the ceiling to $100), an outage reverts decisions to the looser
  shipped defaults ($250) until config is reachable again - the fallback is
  safe relative to the *shipped* posture, not relative to a stricter override.
- Alternatives visible in the design space: fail closed (escalate everything
  while config is down), cache the last-known-good thresholds, or crash and
  let the workflow retry.

## Decision

When the config service is unreachable, the decision service falls back to the shipped policy.md defaults (AutonomyThresholds() - $250 / 0.80) rather than failing closed, caching last-known-good, or crashing:

1. The fallback is client-side in the decision service - fetch_thresholds catches any invoke exception, validation error, or empty response, logs one thresholds_fetch_failed warning, and returns the hardcoded defaults. The config service holds no fallback logic of its own.
2. The safe posture is never looser than the defaults: because the shipped defaults are the tightest baseline I guarantee, an outage can never widen the autonomy ceiling or lower the confidence bar.
3. The pipeline never crashes and never silently loosens - every money decision is still made against a known, conservative policy, and the fallback is observable via the warning line.
I reject the alternatives for this system: fail-closed (escalate everything) turns a config blip into a full autonomy outage that floods human review; last-known-good caching adds a staleness/invalidation surface and could itself serve an out-of-date loosened value; crash-and-retry converts a recoverable degradation into an availability failure of the money path.

## Consequences

Positive. A config outage degrades gracefully instead of stopping the money path: decisions continue against the conservative shipped baseline, the failure is loud in logs (thresholds_fetch_failed) but never loud to the user, and the invariant that matters - an outage cannot widen autonomy - holds by construction because the fallback value is the tightest guaranteed posture. Keeping the logic client-side means the guarantee does not depend on the config service being healthy enough to serve a fallback, which is exactly when I would not be able to trust it.

Trade-offs. The fallback is safe relative to the shipped posture, not relative to a stricter runtime override: if an operator had tightened the ceiling at runtime (say to $100), an outage reverts decisions to the looser $250 default until config is reachable again. I accept that because the defaults are chosen to be a safe floor and a config outage is expected to be short, but it is a real semantic gap - "fail safe" here means "safe relative to what I shipped," not "safe relative to the last human intent." I also duplicate the policy numbers in two places (defaults.py and the config store), which must be kept in sync.

Risks and follow-ups. The honest exposure is a silent tightening reversion: because the fallback is invisible to the submitter and only warns in logs, a prolonged outage could quietly run the system looser than an operator intended without anyone noticing unless the warning is alerted on - so that log line should be wired to monitoring, not just written. If the business ever needs runtime tightening to survive an outage, this design is the wrong one and last-known-good caching (with its own invalidation cost) would be revisited. The duplicated defaults are a drift risk: a change to policy.md §6 must land in both defaults.py and any seeded config value, or the two sources disagree.
