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

    async def commit_budget_and_reservation(
        self, department: str, available: Decimal, etag: str | None, reservation: Reservation
    ) -> bool:
        # One Dapr transaction: the budget CAS (first-write under its etag) and
        # the reservation upsert commit together, so a crash can never debit the
        # budget without recording the reservation.
        budget_req: dict = {"key": f"budget::{department}", "value": {"available": str(available)}}
        if etag is not None:
            budget_req["etag"] = etag
            budget_req["options"] = {"concurrency": "first-write", "consistency": "strong"}
        operations = [
            {"operation": "upsert", "request": budget_req},
            {
                "operation": "upsert",
                "request": {
                    "key": f"reservation::{reservation.tracking_id}",
                    "value": reservation.model_dump(mode="json"),
                },
            },
        ]
        return await self._dapr.try_execute_transaction(STATE_STORE, operations)

    async def get_reservation(self, tracking_id: str) -> Reservation | None:
        value = await self._dapr.get_state(STATE_STORE, f"reservation::{tracking_id}")
        return Reservation.model_validate(value) if value else None

    async def put_reservation(self, reservation: Reservation) -> None:
        await self._dapr.save_state(
            STATE_STORE,
            f"reservation::{reservation.tracking_id}",
            reservation.model_dump(mode="json"),
        )
