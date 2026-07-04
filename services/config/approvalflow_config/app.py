"""Config service HTTP surface."""

from __future__ import annotations

from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app

from .defaults import DEFAULT_POLICY_SUMMARY, DEFAULT_THRESHOLDS

log = get_logger()

STATE_STORE = "statestore"
THRESHOLDS_KEY = "thresholds"


def build_app(dapr: DaprIO | None = None):
    app = create_app("config")
    dapr = dapr or DaprIO()
    app.state.dapr = dapr

    @app.get("/config/thresholds", tags=["config"])
    async def get_thresholds() -> dict:
        """Current autonomy thresholds. Falls back to the policy.md defaults
        until a controller overrides them (so the pipeline always has values)."""
        stored = await dapr.get_state(STATE_STORE, THRESHOLDS_KEY)
        if stored is None:
            log.info("thresholds_default_served")
            return DEFAULT_THRESHOLDS
        return stored  # type: ignore[return-value]

    @app.put("/config/thresholds", tags=["config"])
    async def put_thresholds(thresholds: dict) -> dict:
        """Update thresholds at runtime — no redeploy (F7/M13)."""
        merged = {**DEFAULT_THRESHOLDS, **thresholds}
        await dapr.save_state(STATE_STORE, THRESHOLDS_KEY, merged)
        log.info("thresholds_updated", keys=sorted(thresholds.keys()))
        return merged

    @app.get("/config/policy", tags=["config"])
    async def get_policy() -> dict:
        return {"summary": DEFAULT_POLICY_SUMMARY}

    return app


app = build_app()
