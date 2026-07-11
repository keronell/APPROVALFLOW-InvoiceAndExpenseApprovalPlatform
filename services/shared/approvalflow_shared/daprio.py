"""Thin async helpers over the Dapr sidecar HTTP API (M5).

Shared by services that publish events, read/write state, or invoke another
service synchronously. Keeps every service off the raw URLs and consistent
about correlation-id propagation.
"""

from __future__ import annotations

import os

import httpx
from pydantic import BaseModel

from .contracts import Topics
from .correlation import CORRELATION_HEADER


def dapr_base_url() -> str:
    port = os.environ.get("DAPR_HTTP_PORT", "3500")
    return f"http://localhost:{port}/v1.0"


class DaprIO:
    """One shared httpx client per service instance."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def publish(self, topic: str, event: BaseModel, *, correlation_id: str) -> None:
        resp = await self._client.post(
            f"{dapr_base_url()}/publish/{Topics.PUBSUB_NAME}/{topic}",
            json=event.model_dump(mode="json", by_alias=True),
            params={"metadata.cloudevent.id": correlation_id},
        )
        resp.raise_for_status()

    async def save_state(self, store: str, key: str, value: object) -> None:
        resp = await self._client.post(
            f"{dapr_base_url()}/state/{store}",
            json=[{"key": key, "value": value}],
        )
        resp.raise_for_status()

    async def get_state(self, store: str, key: str) -> object | None:
        resp = await self._client.get(f"{dapr_base_url()}/state/{store}/{key}")
        if resp.status_code == 204 or not resp.content:
            return None
        resp.raise_for_status()
        return resp.json()

    async def get_state_and_etag(self, store: str, key: str) -> tuple[object | None, str | None]:
        """Read a value together with its ETag for optimistic concurrency (M9)."""
        resp = await self._client.get(f"{dapr_base_url()}/state/{store}/{key}")
        if resp.status_code == 204 or not resp.content:
            return None, None
        resp.raise_for_status()
        return resp.json(), resp.headers.get("ETag")

    @staticmethod
    def _is_conflict(resp: httpx.Response) -> bool:
        """Dapr reports optimistic-concurrency conflicts as 409/412 — except the
        Redis component (observed on 1.18), which surfaces the first-write/etag
        Lua-script rejection as a 500 ERR_STATE_SAVE whose message reads
        "failed to set key ..."."""
        if resp.status_code in (409, 412):
            return True
        if resp.status_code == 500:
            text = resp.text.lower()
            return "etag" in text or "failed to set key" in text
        return False

    async def try_save_state(
        self, store: str, key: str, value: object, *, etag: str | None = None
    ) -> bool:
        """Conditional write. With an ETag it uses first-write concurrency and
        returns False on a conflict so the caller can retry — this is what
        prevents two concurrent approvals from overspending a budget."""
        entry: dict = {"key": key, "value": value}
        if etag is not None:
            entry["etag"] = etag
            entry["options"] = {"concurrency": "first-write", "consistency": "strong"}
        resp = await self._client.post(f"{dapr_base_url()}/state/{store}", json=[entry])
        if self._is_conflict(resp):
            return False
        resp.raise_for_status()
        return True

    async def try_execute_transaction(self, store: str, operations: list[dict]) -> bool:
        """Commit several state operations atomically (all-or-nothing).

        ``operations`` are Dapr transaction ops, e.g.
        ``{"operation": "upsert", "request": {"key": ..., "value": ...,
        "etag": ..., "options": {"concurrency": "first-write",
        "consistency": "strong"}}}``. Returns ``False`` on an optimistic-
        concurrency conflict so the caller can re-read and retry — this is what
        lets a budget debit and its reservation commit as one unit (M9/M10).
        The Redis state store supports transactions.
        """
        resp = await self._client.post(
            f"{dapr_base_url()}/state/{store}/transaction",
            json={"operations": operations},
        )
        if self._is_conflict(resp):
            return False
        resp.raise_for_status()
        return True

    async def try_insert_state(self, store: str, key: str, value: object) -> bool:
        """Atomic insert-if-absent (SETNX semantics): first-write concurrency
        with no ETag succeeds only when the key does not exist yet. Returns
        False if someone else already owns the key — the race-safe primitive
        behind duplicate/idempotency claims (M10)."""
        entry = {
            "key": key,
            "value": value,
            "options": {"concurrency": "first-write", "consistency": "strong"},
        }
        resp = await self._client.post(f"{dapr_base_url()}/state/{store}", json=[entry])
        if self._is_conflict(resp):
            return False
        resp.raise_for_status()
        return True

    async def invoke(
        self,
        app_id: str,
        method: str,
        *,
        verb: str = "GET",
        data: object | None = None,
        correlation_id: str | None = None,
    ) -> object | None:
        """Synchronous service-to-service call via Dapr service invocation."""
        headers = {CORRELATION_HEADER: correlation_id} if correlation_id else {}
        resp = await self._client.request(
            verb,
            f"{dapr_base_url()}/invoke/{app_id}/method/{method}",
            json=data,
            headers=headers,
        )
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def aclose(self) -> None:
        await self._client.aclose()
