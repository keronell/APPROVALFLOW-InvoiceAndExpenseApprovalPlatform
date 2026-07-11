"""Dapr-backed implementations of the store and publisher (M5).

Selected at runtime via ``STORE_BACKEND`` / ``PUBLISHER_BACKEND`` (default
``dapr``); tests use the in-memory implementations instead.

Duplicate/idempotency claims use ``DaprIO.try_insert_state`` — an atomic
insert-if-absent (first-write concurrency, SETNX semantics). Two concurrent
submissions with the same fingerprint race on the insert; exactly one wins and
the loser reads back the winner's tracking id, so the same business invoice can
never be processed or paid twice (M10/GLOBAL-DUP).
"""

from __future__ import annotations

from approvalflow_shared.daprio import DaprIO
from approvalflow_shared.logging import get_logger
from pydantic import BaseModel

from .store import SubmissionRecord

log = get_logger()

STATE_STORE = "statestore"
_CLAIM_ATTEMPTS = 3


class DaprSubmissionStore:
    def __init__(self, dapr: DaprIO | None = None) -> None:
        self._dapr = dapr or DaprIO()

    async def get(self, tracking_id: str) -> SubmissionRecord | None:
        value = await self._dapr.get_state(STATE_STORE, tracking_id)
        return SubmissionRecord.model_validate(value) if value else None

    async def put(self, record: SubmissionRecord) -> None:
        await self._dapr.save_state(
            STATE_STORE, record.tracking_id, record.model_dump(mode="json")
        )

    async def claim_fingerprint(self, fingerprint: str, tracking_id: str) -> str:
        return await self._claim(f"fp::{fingerprint}", tracking_id)

    async def claim_idempotency_key(self, key: str, tracking_id: str) -> str:
        return await self._claim(f"idem::{key}", tracking_id)

    async def _claim(self, key: str, tracking_id: str) -> str:
        """Atomically claim ``key``; return the owning tracking id."""
        for _ in range(_CLAIM_ATTEMPTS):
            if await self._dapr.try_insert_state(STATE_STORE, key, tracking_id):
                return tracking_id
            existing = await self._dapr.get_state(STATE_STORE, key)
            if existing is not None:
                return str(existing)
            # Insert conflicted but the key reads empty (owner deleted between
            # calls?) — retry rather than risk letting a duplicate through.
        raise RuntimeError(f"could not resolve claim for {key}")


class DaprPublisher:
    def __init__(self, dapr: DaprIO | None = None) -> None:
        self._dapr = dapr or DaprIO()

    async def publish(self, topic: str, event: BaseModel, *, correlation_id: str) -> None:
        await self._dapr.publish(topic, event, correlation_id=correlation_id)
        log.info("event_published", topic=topic, correlation_id=correlation_id)
