"""Budget ledger: reservations, compensation, idempotency, no overspend (M9/M10)."""

import asyncio
from decimal import Decimal

import pytest
from approvalflow_payment.budget import (
    BudgetLedger,
    InMemoryBudgetStore,
    ReservationStatus,
)


@pytest.fixture
def store() -> InMemoryBudgetStore:
    return InMemoryBudgetStore({"marketing-2026Q2": "1000"})


@pytest.mark.anyio
async def test_reserve_then_confirm_decrements_budget(store):
    ledger = BudgetLedger(store)
    result = await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))
    assert result.ok
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")
    await ledger.confirm("t1")
    assert (await store.get_reservation("t1")).status == ReservationStatus.CONFIRMED
    # Confirm does not change the budget.
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")


@pytest.mark.anyio
async def test_release_restores_budget(store):
    ledger = BudgetLedger(store)
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))
    await ledger.release("t1")
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("1000")
    assert (await store.get_reservation("t1")).status == ReservationStatus.RELEASED


@pytest.mark.anyio
async def test_over_budget_is_rejected(store):
    ledger = BudgetLedger(store)
    assert (await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))).ok
    result = await ledger.reserve("t2", "marketing-2026Q2", Decimal("600"))
    assert not result.ok and "insufficient" in result.reason
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")  # unchanged by the rejected reservation


@pytest.mark.anyio
async def test_reserve_is_idempotent(store):
    ledger = BudgetLedger(store)
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))  # same tracking id
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")  # deducted once, not twice


@pytest.mark.anyio
async def test_release_is_idempotent(store):
    ledger = BudgetLedger(store)
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("600"))
    await ledger.release("t1")
    await ledger.release("t1")  # second release is a no-op
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("1000")


@pytest.mark.anyio
async def test_concurrent_reservations_never_overspend(store):
    """INV-1014A/B shape: two $600 reservations against a $1000 budget — exactly
    one succeeds and the budget never goes negative (M9)."""
    ledger = BudgetLedger(store)
    a, b = await asyncio.gather(
        ledger.reserve("t-a", "marketing-2026Q2", Decimal("600")),
        ledger.reserve("t-b", "marketing-2026Q2", Decimal("600")),
    )
    assert [a.ok, b.ok].count(True) == 1
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")
    assert available >= 0


class _FlakyStore(InMemoryBudgetStore):
    """Fails the first conditional write to simulate an ETag conflict."""

    def __init__(self, budgets):
        super().__init__(budgets)
        self._failed_once = False

    async def try_set_budget(self, department, available, etag):
        if not self._failed_once:
            self._failed_once = True
            return False  # simulate a lost race
        return await super().try_set_budget(department, available, etag)


@pytest.mark.anyio
async def test_reserve_retries_after_etag_conflict():
    store = _FlakyStore({"marketing-2026Q2": "1000"})
    result = await BudgetLedger(store).reserve("t1", "marketing-2026Q2", Decimal("600"))
    assert result.ok  # retried past the conflict
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")
