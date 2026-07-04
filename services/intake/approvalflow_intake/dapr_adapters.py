"""Dapr-backed implementations of the store and publisher (M5).

Talks to the Dapr sidecar over its HTTP API. Selected at runtime via
``STORE_BACKEND`` / ``PUBLISHER_BACKEND`` (default ``dapr``); tests use the
in-memory implementations instead.

Note: ``claim_*`` uses a get-then-put and is not strictly atomic under a race.
Intake dedup is backstopped downstream (the decision workflow and payment are
keyed by tracking id and fingerprint), and the real no-overspend concurrency
guarantee is enforced with ETag transactions in the payment service.
"""

from __future__ import annotations

import os

import httpx
from approvalflow_shared.logging import get_logger
from pydantic import BaseModel

from .store import SubmissionRecord

log = get_logger()

STATE_STORE = "statestore"


def _dapr_base_url() -> str:
    port = os.environ.get("DAPR_HTTP_PORT", "3500")
    return f"http://localhost:{port}/v1.0"


class DaprSubmissionStore:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def get(self, tracking_id: str) -> SubmissionRecord | None:
        resp = await self._client.get(f"{_dapr_base_url()}/state/{STATE_STORE}/{tracking_id}")
        if resp.status_code == 204 or not resp.content:
            return None
        resp.raise_for_status()
        return SubmissionRecord.model_validate(resp.json())

    async def put(self, record: SubmissionRecord) -> None:
        await self._save(record.tracking_id, record.model_dump(mode="json"))

    async def claim_fingerprint(self, fingerprint: str, tracking_id: str) -> str:
        return await self._claim(f"fp::{fingerprint}", tracking_id)

    async def claim_idempotency_key(self, key: str, tracking_id: str) -> str:
        return await self._claim(f"idem::{key}", tracking_id)

    async def _claim(self, key: str, tracking_id: str) -> str:
        existing = await self._get_raw(key)
        if existing is not None:
            return existing
        await self._save(key, tracking_id)
        return tracking_id

    async def _get_raw(self, key: str) -> str | None:
        resp = await self._client.get(f"{_dapr_base_url()}/state/{STATE_STORE}/{key}")
        if resp.status_code == 204 or not resp.content:
            return None
        resp.raise_for_status()
        return resp.json()

    async def _save(self, key: str, value: object) -> None:
        resp = await self._client.post(
            f"{_dapr_base_url()}/state/{STATE_STORE}",
            json=[{"key": key, "value": value}],
        )
        resp.raise_for_status()


class DaprPublisher:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=5.0)

    async def publish(self, topic: str, event: BaseModel, *, correlation_id: str) -> None:
        from approvalflow_shared.contracts import Topics

        resp = await self._client.post(
            f"{_dapr_base_url()}/publish/{Topics.PUBSUB_NAME}/{topic}",
            json=event.model_dump(mode="json", by_alias=True),
            params={"metadata.cloudevent.id": correlation_id},
        )
        resp.raise_for_status()
        log.info("event_published", topic=topic, correlation_id=correlation_id)
