"""Intake business logic: async accept, dedupe, idempotency (F1/F3/M8/M10)."""

import pytest
from approvalflow_intake.publisher import InMemoryPublisher
from approvalflow_intake.service import IntakeService
from approvalflow_intake.store import InMemorySubmissionStore
from approvalflow_shared.contracts import (
    InvoiceSubmission,
    ItemStatus,
    StatusChanged,
    Topics,
)


@pytest.fixture
def store() -> InMemorySubmissionStore:
    return InMemorySubmissionStore()


@pytest.fixture
def publisher() -> InMemoryPublisher:
    return InMemoryPublisher()


@pytest.fixture
def service(store, publisher) -> IntakeService:
    return IntakeService(store, publisher)


def invoice(fixtures_by_id, fixture_id: str) -> InvoiceSubmission:
    return InvoiceSubmission.model_validate(fixtures_by_id[fixture_id])


@pytest.mark.anyio
async def test_fresh_submission_is_accepted_and_published(service, publisher, fixtures_by_id):
    accepted = await service.submit(invoice(fixtures_by_id, "INV-1001"), correlation_id="corr-1")

    assert accepted.tracking_id == "corr-1"  # tracking id == correlation id
    assert accepted.status == ItemStatus.RECEIVED
    assert accepted.status_url == "/api/invoices/corr-1"
    assert publisher.topics() == [Topics.ITEM_SUBMITTED]


@pytest.mark.anyio
async def test_duplicate_fingerprint_short_circuits_without_publishing(
    service, publisher, fixtures_by_id
):
    await service.submit(invoice(fixtures_by_id, "INV-1001"), correlation_id="corr-original")
    # INV-1007 is an exact re-submission of INV-1001 (same fingerprint).
    dup = await service.submit(invoice(fixtures_by_id, "INV-1007"), correlation_id="corr-dup")

    assert dup.status == ItemStatus.DUPLICATE
    # Only the first submission was published — no second agent call / payment.
    assert publisher.topics() == [Topics.ITEM_SUBMITTED]
    record = await service.get_status("corr-dup")
    assert record.duplicate_of == "corr-original"


@pytest.mark.anyio
async def test_idempotency_key_replay_returns_same_tracking_id(service, publisher, fixtures_by_id):
    inv = invoice(fixtures_by_id, "INV-1001")
    first = await service.submit(inv, correlation_id="corr-a", idempotency_key="key-1")
    second = await service.submit(inv, correlation_id="corr-b", idempotency_key="key-1")

    assert first.tracking_id == second.tracking_id == "corr-a"
    # Exactly one effect: published once despite two POSTs.
    assert publisher.topics() == [Topics.ITEM_SUBMITTED]


@pytest.mark.anyio
async def test_idempotency_key_claimed_but_record_not_yet_persisted(
    service, store, publisher, fixtures_by_id
):
    """Race: the original request has claimed the key but not yet persisted its
    record. A retry must return the owner's tracking id and do NO new work —
    never fall through to publish a duplicate."""
    # Simulate the in-flight original: key owned by 'corr-a', but get('corr-a') None.
    assert await store.claim_idempotency_key("key-1", "corr-a") == "corr-a"

    second = await service.submit(
        invoice(fixtures_by_id, "INV-1001"), correlation_id="corr-b", idempotency_key="key-1"
    )

    assert second.tracking_id == "corr-a"
    assert second.status == ItemStatus.RECEIVED
    assert publisher.topics() == []  # nothing published under the retry
    # No duplicate record was created for the retry's tracking id.
    assert await service.get_status("corr-b") is None


@pytest.mark.anyio
async def test_status_change_event_updates_record(service, fixtures_by_id):
    await service.submit(invoice(fixtures_by_id, "INV-1001"), correlation_id="corr-1")
    await service.apply_status_change(
        StatusChanged(
            tracking_id="corr-1",
            correlation_id="corr-1",
            status=ItemStatus.AUTO_APPROVED,
            reason="Within ceiling; auto-approved.",
        )
    )
    record = await service.get_status("corr-1")
    assert record.status == ItemStatus.AUTO_APPROVED
    assert "auto-approved" in record.reason.lower()


@pytest.mark.anyio
async def test_status_change_for_unknown_item_is_ignored(service):
    await service.apply_status_change(
        StatusChanged(
            tracking_id="nope", correlation_id="nope", status=ItemStatus.PAID, reason="x"
        )
    )
    assert await service.get_status("nope") is None
