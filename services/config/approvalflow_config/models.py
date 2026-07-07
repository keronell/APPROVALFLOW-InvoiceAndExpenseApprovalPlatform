"""Validated threshold schema for the config service.

The mutation endpoint must never accept arbitrary values: a caller raising
`ceiling_usd` or dropping `min_confidence` would widen the autonomy posture.
Unknown keys are rejected outright (extra="forbid").
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# Sanity cap for the autonomy ceiling: far above every per-category cap, far
# below "unbounded". Raising it is a deliberate code change, not a config PUT.
CEILING_HARD_CAP = Decimal("10000")


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ceiling_usd: Decimal = Field(ge=0, le=CEILING_HARD_CAP)
    min_confidence: float = Field(ge=0.0, le=1.0)

    meal_per_attendee_usd: Decimal = Field(gt=0)
    client_entertainment_over_usd: Decimal = Field(gt=0)
    saas_monthly_cap_usd: Decimal = Field(gt=0)
    hardware_cap_usd: Decimal = Field(gt=0)
    travel_single_over_usd: Decimal = Field(gt=0)
    receipt_required_over_usd: Decimal = Field(ge=0)
    fx_hard_stop_usd: Decimal = Field(gt=0)

    fx_rates: dict[str, Decimal]
