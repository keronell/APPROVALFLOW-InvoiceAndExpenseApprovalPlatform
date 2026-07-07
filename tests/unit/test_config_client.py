"""fetch_thresholds fail-safe: bad data can never loosen the posture (ADR-004)."""

from decimal import Decimal

import pytest
from approvalflow_decision.models import AutonomyThresholds
from approvalflow_decision.service.config_client import fetch_thresholds
from pydantic import ValidationError


class FakeDapr:
    def __init__(self, response):
        self._response = response

    async def invoke(self, app_id, method, *, verb="GET", data=None, correlation_id=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _defaults() -> AutonomyThresholds:
    return AutonomyThresholds()


@pytest.mark.anyio
async def test_valid_response_is_used():
    data = _defaults().model_dump(mode="json") | {"ceiling_usd": "100"}
    thresholds = await fetch_thresholds(FakeDapr(data))
    assert thresholds.ceiling_usd == Decimal("100")


@pytest.mark.anyio
async def test_unreachable_config_falls_back_to_defaults():
    thresholds = await fetch_thresholds(FakeDapr(ConnectionError("config down")))
    assert thresholds == _defaults()


@pytest.mark.anyio
async def test_poisoned_fx_rate_falls_back_to_defaults():
    """A stored zero FX rate would convert totals to $0 and bypass every money
    gate — validation must reject it and fall back to the safe defaults."""
    poisoned = _defaults().model_dump(mode="json") | {"fx_rates": {"USD": "0"}}
    with pytest.raises(ValidationError):
        AutonomyThresholds.model_validate(poisoned)  # the model itself refuses
    thresholds = await fetch_thresholds(FakeDapr(poisoned))
    assert thresholds == _defaults()  # and the fetch fails safe
