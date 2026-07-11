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
async def test_non_positive_amounts_are_rejected(store):
    """A negative reservation would INCREASE the budget — must be refused."""
    ledger = BudgetLedger(store)
    for amount in (Decimal("-10"), Decimal("0")):
        result = await ledger.reserve("t-neg", "marketing-2026Q2", amount)
        assert not result.ok and "non-positive" in result.reason
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("1000")  # untouched


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
    """Fails the first atomic commit to simulate an ETag conflict."""

    def __init__(self, budgets):
        super().__init__(budgets)
        self._failed_once = False

    async def commit_budget_and_reservation(self, department, available, etag, reservation):
        if not self._failed_once:
            self._failed_once = True
            return False  # simulate a lost race
        return await super().commit_budget_and_reservation(
            department, available, etag, reservation
        )


@pytest.mark.anyio
async def test_reserve_retries_after_etag_conflict():
    store = _FlakyStore({"marketing-2026Q2": "1000"})
    result = await BudgetLedger(store).reserve("t1", "marketing-2026Q2", Decimal("600"))
    assert result.ok  # retried past the conflict
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("400")


@pytest.mark.anyio
async def test_commit_is_atomic_on_conflict(store):
    """The budget debit and its reservation are one unit: on an ETag conflict
    NEITHER lands — no debited budget with a missing reservation."""
    from approvalflow_payment.budget import Reservation

    available, etag = await store.get_budget("marketing-2026Q2")
    # A concurrent writer bumps the version, making our etag stale.
    assert await store.commit_budget_and_reservation(
        "marketing-2026Q2", Decimal("900"), etag,
        Reservation(tracking_id="winner", department="marketing-2026Q2",
                    amount="100", status=ReservationStatus.RESERVED),
    )
    losing = Reservation(tracking_id="loser", department="marketing-2026Q2",
                         amount="500", status=ReservationStatus.RESERVED)
    assert not await store.commit_budget_and_reservation(
        "marketing-2026Q2", Decimal("400"), etag, losing
    )
    after, _ = await store.get_budget("marketing-2026Q2")
    assert after == Decimal("900")  # only the winner's debit landed
    assert await store.get_reservation("loser") is None  # no orphaned reservation


@pytest.mark.anyio
async def test_redelivery_never_double_debits():
    """Redelivery of the same reservation (small amount, ample budget) debits
    exactly once — the reservation committed atomically with the debit makes the
    replay a no-op."""
    store = InMemoryBudgetStore({"marketing-2026Q2": "1000"})
    ledger = BudgetLedger(store)
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("300"))
    await ledger.reserve("t1", "marketing-2026Q2", Decimal("300"))  # redelivery
    available, _ = await store.get_budget("marketing-2026Q2")
    assert available == Decimal("700")  # 300 once, not 600
