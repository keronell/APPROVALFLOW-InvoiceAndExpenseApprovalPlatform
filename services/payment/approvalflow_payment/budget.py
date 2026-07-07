"""Department budget ledger with optimistic-concurrency reservations.

The ledger depends on a :class:`BudgetStore` protocol (DIP), so the
no-overspend logic is unit-tested with an in-memory store that models ETags,
and runs against Dapr state in production.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

# Seed budgets (from Examples/sample-invoices.json → budgets). Unknown
# departments start at 0 so they cannot spend until funded.
DEFAULT_BUDGETS: dict[str, str] = {
    "marketing-2026Q2": "1000",
    "engineering-2026Q2": "50000",
    "sales-2026Q2": "20000",
}


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    CONFIRMED = "confirmed"
    RELEASED = "released"


class Reservation(BaseModel):
    tracking_id: str
    department: str
    amount: str
    status: ReservationStatus


class ReserveResult(BaseModel):
    ok: bool
    reason: str = ""


@runtime_checkable
class BudgetStore(Protocol):
    async def get_budget(self, department: str) -> tuple[Decimal, str | None]:
        """Return (available, etag), seeding the default on first access."""
        ...

    async def try_set_budget(self, department: str, available: Decimal, etag: str | None) -> bool:
        """Conditional write; False on ETag conflict."""
        ...

    async def get_reservation(self, tracking_id: str) -> Reservation | None: ...

    async def put_reservation(self, reservation: Reservation) -> None: ...


class BudgetLedger:
    def __init__(self, store: BudgetStore, *, retries: int = 8) -> None:
        self._store = store
        self._retries = retries

    async def reserve(self, tracking_id: str, department: str, amount: Decimal) -> ReserveResult:
        # Defense in depth (contracts already forbid non-positive totals): a
        # negative reservation would INCREASE the budget on this subtraction.
        if amount <= 0:
            return ReserveResult(ok=False, reason="non-positive amount")

        # Idempotent: an existing reservation means this was already handled.
        existing = await self._store.get_reservation(tracking_id)
        active = (ReservationStatus.RESERVED, ReservationStatus.CONFIRMED)
        if existing and existing.status in active:
            return ReserveResult(ok=True, reason="already reserved")

        for _ in range(self._retries):
            available, etag = await self._store.get_budget(department)
            if amount > available:
                return ReserveResult(ok=False, reason="insufficient budget")
            if await self._store.try_set_budget(department, available - amount, etag):
                await self._store.put_reservation(
                    Reservation(
                        tracking_id=tracking_id,
                        department=department,
                        amount=str(amount),
                        status=ReservationStatus.RESERVED,
                    )
                )
                return ReserveResult(ok=True)
            # ETag conflict — someone else changed the budget; re-read and retry.
        return ReserveResult(ok=False, reason="budget contention")

    async def release(self, tracking_id: str) -> None:
        """Compensation: give the reserved amount back. Idempotent."""
        reservation = await self._store.get_reservation(tracking_id)
        if reservation is None or reservation.status != ReservationStatus.RESERVED:
            return
        amount = Decimal(reservation.amount)
        for _ in range(self._retries):
            available, etag = await self._store.get_budget(reservation.department)
            if await self._store.try_set_budget(reservation.department, available + amount, etag):
                reservation.status = ReservationStatus.RELEASED
                await self._store.put_reservation(reservation)
                return
        raise RuntimeError(f"could not release budget for {tracking_id}")

    async def confirm(self, tracking_id: str) -> None:
        """Commit the reservation (money spent). No budget change. Idempotent."""
        reservation = await self._store.get_reservation(tracking_id)
        if reservation is None or reservation.status != ReservationStatus.RESERVED:
            return
        reservation.status = ReservationStatus.CONFIRMED
        await self._store.put_reservation(reservation)


class InMemoryBudgetStore:
    """Test store that models ETags with a monotonically increasing version."""

    def __init__(self, budgets: dict[str, str] | None = None) -> None:
        self._budgets: dict[str, tuple[Decimal, int]] = {
            dept: (Decimal(v), 0) for dept, v in (budgets or DEFAULT_BUDGETS).items()
        }
        self._reservations: dict[str, Reservation] = {}
        self._lock = asyncio.Lock()

    async def get_budget(self, department: str) -> tuple[Decimal, str | None]:
        if department not in self._budgets:
            self._budgets[department] = (Decimal(DEFAULT_BUDGETS.get(department, "0")), 0)
        available, version = self._budgets[department]
        return available, str(version)

    async def try_set_budget(self, department: str, available: Decimal, etag: str | None) -> bool:
        async with self._lock:
            _, version = self._budgets[department]
            if etag is not None and str(version) != etag:
                return False  # conflict
            self._budgets[department] = (available, version + 1)
            return True

    async def get_reservation(self, tracking_id: str) -> Reservation | None:
        return self._reservations.get(tracking_id)

    async def put_reservation(self, reservation: Reservation) -> None:
        self._reservations[reservation.tracking_id] = reservation
