"""Dapr-state-backed BudgetStore (production)."""

from __future__ import annotations

from decimal import Decimal

from approvalflow_shared.daprio import DaprIO

from .budget import DEFAULT_BUDGETS, Reservation

STATE_STORE = "statestore"


class DaprBudgetStore:
    def __init__(self, dapr: DaprIO) -> None:
        self._dapr = dapr

    async def get_budget(self, department: str) -> tuple[Decimal, str | None]:
        key = f"budget::{department}"
        value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
        if value is None:
            # Seed the department's starting budget on first access.
            default = DEFAULT_BUDGETS.get(department, "0")
            await self._dapr.try_save_state(STATE_STORE, key, {"available": default})
            value, etag = await self._dapr.get_state_and_etag(STATE_STORE, key)
        return Decimal(str(value["available"])), etag  # type: ignore[index]

    async def try_set_budget(self, department: str, available: Decimal, etag: str | None) -> bool:
        return await self._dapr.try_save_state(
            STATE_STORE, f"budget::{department}", {"available": str(available)}, etag=etag
        )

    async def get_reservation(self, tracking_id: str) -> Reservation | None:
        value = await self._dapr.get_state(STATE_STORE, f"reservation::{tracking_id}")
        return Reservation.model_validate(value) if value else None

    async def put_reservation(self, reservation: Reservation) -> None:
        await self._dapr.save_state(
            STATE_STORE,
            f"reservation::{reservation.tracking_id}",
            reservation.model_dump(mode="json"),
        )
