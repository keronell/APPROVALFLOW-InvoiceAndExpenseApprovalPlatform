# ADR-004 — Config client fails safe to policy.md defaults on outage

- Status: context drafted — decision/consequences pending (author)
- Date: 2026-07-07

## Context

Autonomy thresholds must be changeable at runtime without a redeploy (F7/M13):
the config service serves them from the Dapr state store
(`PUT /config/thresholds` overrides at runtime; defaults in
`services/config/approvalflow_config/defaults.py` mirror `policy.md` §6 —
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

- The fallback is entirely **client-side in the decision service** — the config
  service has no fallback logic of its own. Any invoke exception, validation
  error, or empty response yields the hardcoded `AutonomyThresholds()` defaults
  ($250 / 0.80, policy.md §6), with one `thresholds_fetch_failed` warning.
  The pipeline never crashes and never silently loosens.
- Module docstring (lines 3–5) states the intent: *"the safe posture is never
  looser than the defaults, so a config outage cannot widen the autonomy
  ceiling."*
- Honest caveat: if a controller had **tightened** thresholds at runtime (e.g.
  lowered the ceiling to $100), an outage reverts decisions to the looser
  shipped defaults ($250) until config is reachable again — the fallback is
  safe relative to the *shipped* posture, not relative to a stricter override.
- Alternatives visible in the design space: fail closed (escalate everything
  while config is down), cache the last-known-good thresholds, or crash and
  let the workflow retry.

## Decision

<!-- TODO: write this yourself — you defend this live -->

## Consequences

<!-- TODO: write this yourself — you defend this live -->
