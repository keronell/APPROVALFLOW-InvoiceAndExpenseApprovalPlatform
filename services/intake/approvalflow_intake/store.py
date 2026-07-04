"""Submission storage abstraction (DIP).

The service depends on the :class:`SubmissionStore` protocol, not on Dapr. An
in-memory implementation backs the tests; the Dapr-backed implementation
(``dapr_adapters``) backs production. ``claim_*`` are atomic set-if-absent
operations returning the *winning* owner, which is what makes duplicate and
redelivery handling race-safe (M10).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Protocol, runtime_checkable

from approvalflow_shared.contracts import InvoiceSubmission, ItemStatus
from pydantic import BaseModel, Field


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class SubmissionRecord(BaseModel):
    """Everything intake knows about one submission (its owned state)."""

    tracking_id: str
    correlation_id: str
    invoice: InvoiceSubmission
    status: ItemStatus
    reason: str
    fingerprint: str
    duplicate_of: str | None = None
    created_at: dt.datetime = Field(default_factory=_utcnow)
    updated_at: dt.datetime = Field(default_factory=_utcnow)


@runtime_checkable
class SubmissionStore(Protocol):
    async def get(self, tracking_id: str) -> SubmissionRecord | None: ...

    async def put(self, record: SubmissionRecord) -> None: ...

    async def claim_fingerprint(self, fingerprint: str, tracking_id: str) -> str:
        """Associate ``fingerprint`` with ``tracking_id`` if unclaimed.

        Returns the tracking id that owns the fingerprint (ours if we won the
        claim, the pre-existing owner otherwise → the caller sees a duplicate).
        """
        ...

    async def claim_idempotency_key(self, key: str, tracking_id: str) -> str:
        """As :meth:`claim_fingerprint`, for request-level idempotency keys."""
        ...


class InMemorySubmissionStore:
    """Thread/async-safe in-memory store for tests and local runs."""

    def __init__(self) -> None:
        self._records: dict[str, SubmissionRecord] = {}
        self._fingerprints: dict[str, str] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get(self, tracking_id: str) -> SubmissionRecord | None:
        return self._records.get(tracking_id)

    async def put(self, record: SubmissionRecord) -> None:
        record.updated_at = _utcnow()
        self._records[record.tracking_id] = record

    async def claim_fingerprint(self, fingerprint: str, tracking_id: str) -> str:
        async with self._lock:
            return self._fingerprints.setdefault(fingerprint, tracking_id)

    async def claim_idempotency_key(self, key: str, tracking_id: str) -> str:
        async with self._lock:
            return self._idempotency.setdefault(key, tracking_id)
