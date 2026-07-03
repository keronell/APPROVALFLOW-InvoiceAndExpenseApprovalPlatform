"""Intake business logic — independent of FastAPI and Dapr."""

from __future__ import annotations

from approvalflow_shared.contracts import (
    InvoiceSubmission,
    ItemStatus,
    ItemSubmitted,
    StatusChanged,
    SubmissionAccepted,
    Topics,
)
from approvalflow_shared.logging import get_logger

from .publisher import EventPublisher
from .store import SubmissionRecord, SubmissionStore

log = get_logger()


class IntakeService:
    """Turns a submission into an accepted, de-duplicated, published item.

    The tracking id *is* the correlation id (one id follows the request
    end-to-end, M14/F9): the caller passes the correlation id resolved by the
    gateway/middleware.
    """

    def __init__(self, store: SubmissionStore, publisher: EventPublisher) -> None:
        self._store = store
        self._publisher = publisher

    async def submit(
        self,
        invoice: InvoiceSubmission,
        correlation_id: str,
        idempotency_key: str | None = None,
    ) -> SubmissionAccepted:
        tracking_id = correlation_id

        # 1) Request-level idempotency: a retried POST with the same key yields
        #    the same tracking id and does no new work (M10).
        if idempotency_key:
            owner = await self._store.claim_idempotency_key(idempotency_key, tracking_id)
            if owner != tracking_id:
                existing = await self._store.get(owner)
                if existing is not None:
                    log.info("intake_idempotent_replay", tracking_id=owner, key=idempotency_key)
                    return self._accepted(existing)

        # 2) Business duplicate: same vendor + invoiceNumber + total (GLOBAL-DUP).
        #    The first submission owns the fingerprint; later ones short-circuit
        #    to `duplicate` with no publish → no second agent call, no second pay.
        fingerprint = invoice.fingerprint
        owner = await self._store.claim_fingerprint(fingerprint, tracking_id)
        if owner != tracking_id:
            record = SubmissionRecord(
                tracking_id=tracking_id,
                correlation_id=correlation_id,
                invoice=invoice,
                status=ItemStatus.DUPLICATE,
                reason=(
                    "This invoice was already submitted (same vendor, invoice "
                    "number, and total). It will not be paid twice."
                ),
                fingerprint=fingerprint,
                duplicate_of=owner,
            )
            await self._store.put(record)
            log.info("intake_duplicate", tracking_id=tracking_id, duplicate_of=owner)
            return self._accepted(record)

        # 3) Fresh submission: persist, then publish for the pipeline.
        record = SubmissionRecord(
            tracking_id=tracking_id,
            correlation_id=correlation_id,
            invoice=invoice,
            status=ItemStatus.RECEIVED,
            reason="Received. Awaiting an automated policy decision.",
            fingerprint=fingerprint,
        )
        await self._store.put(record)
        await self._publisher.publish(
            Topics.ITEM_SUBMITTED,
            ItemSubmitted(
                tracking_id=tracking_id, correlation_id=correlation_id, invoice=invoice
            ),
            correlation_id=correlation_id,
        )
        log.info("intake_accepted", tracking_id=tracking_id, invoice_id=invoice.id)
        return self._accepted(record)

    async def get_status(self, tracking_id: str) -> SubmissionRecord | None:
        return await self._store.get(tracking_id)

    async def apply_status_change(self, event: StatusChanged) -> None:
        """Update the stored status from a downstream ``item.status-changed``
        event so the submitter's status view stays current (F2)."""
        record = await self._store.get(event.tracking_id)
        if record is None:
            log.warning("intake_status_for_unknown_item", tracking_id=event.tracking_id)
            return
        record.status = event.status
        record.reason = event.reason
        await self._store.put(record)
        log.info("intake_status_updated", tracking_id=event.tracking_id, status=event.status)

    @staticmethod
    def _accepted(record: SubmissionRecord) -> SubmissionAccepted:
        return SubmissionAccepted(
            tracking_id=record.tracking_id,
            status=record.status,
            status_url=f"/api/invoices/{record.tracking_id}",
        )
