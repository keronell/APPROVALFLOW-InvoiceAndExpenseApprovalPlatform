"""Config service HTTP surface."""

from __future__ import annotations

import os
from typing import Annotated

from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from approvalflow_shared.web import create_app
from fastapi import Header, HTTPException
from pydantic import ValidationError

from .defaults import DEFAULT_POLICY_SUMMARY, DEFAULT_THRESHOLDS
from .models import Thresholds

log = get_logger()

STATE_STORE = "statestore"
THRESHOLDS_KEY = "thresholds"
TOKEN_ENV = "CONFIG_API_TOKEN"


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
    async def put_thresholds(
        patch: dict,
        token: Annotated[str | None, Header(alias="X-Config-Token")] = None,
    ) -> dict:
        """Update thresholds at runtime — no redeploy (F7/M13).

        Protected: requires the controller token, rejects unknown keys, and
        validates every value against bounds (ceiling capped, confidence in
        [0,1], money non-negative). An unauthenticated or malformed request
        can never widen the autonomy posture.
        """
        expected = os.environ.get(TOKEN_ENV)
        if not expected:
            raise HTTPException(status_code=403, detail="Threshold mutation is disabled")
        if token != expected:
            raise HTTPException(status_code=401, detail="Invalid or missing X-Config-Token")

        stored = await dapr.get_state(STATE_STORE, THRESHOLDS_KEY) or {}
        merged = {**DEFAULT_THRESHOLDS, **stored, **patch}  # type: ignore[dict-item]
        try:
            validated = Thresholds.model_validate(merged)
        except ValidationError as exc:
            # include_context=False: bound values are Decimals (not JSON-safe).
            errors = exc.errors(include_context=False, include_input=False, include_url=False)
            raise HTTPException(status_code=422, detail=errors) from exc

        payload = validated.model_dump(mode="json")
        await dapr.save_state(STATE_STORE, THRESHOLDS_KEY, payload)
        log.info("thresholds_updated", keys=sorted(patch.keys()))
        return payload

    @app.get("/config/policy", tags=["config"])
    async def get_policy() -> dict:
        return {"summary": DEFAULT_POLICY_SUMMARY}

    return app


app = build_app()
