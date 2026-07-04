"""Fetch autonomy thresholds from the config service via Dapr invocation.

Falls back to the conservative policy.md defaults if config is unreachable —
the safe posture is never *looser* than the defaults, so a config outage cannot
widen the autonomy ceiling.
"""

from __future__ import annotations

from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger

from ..models import AutonomyThresholds

log = get_logger()

CONFIG_APP_ID = "config"


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
