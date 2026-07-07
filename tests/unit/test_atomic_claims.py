"""Race-safe claims: insert-if-absent semantics over the Dapr state API.

Uses an httpx.MockTransport standing in for the sidecar so we can assert the
exact wire behavior: first-write options on the insert, and the losing racer
reading back the winner's tracking id instead of claiming the key.
"""

import json

import httpx
import pytest
from approvalflow_intake.dapr_adapters import DaprSubmissionStore
from approvalflow_shared.daprio import DaprIO


class FakeSidecar:
    """Models a Dapr state store: first-write insert conflicts if the key exists."""

    def __init__(self) -> None:
        self.state: dict[str, object] = {}
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and "/state/" in str(request.url):
            entries = json.loads(request.content)
            self.requests.append(entries[0])
            for entry in entries:
                first_write = entry.get("options", {}).get("concurrency") == "first-write"
                if first_write and "etag" not in entry and entry["key"] in self.state:
                    return httpx.Response(409, text="etag mismatch")
                self.state[entry["key"]] = entry["value"]
            return httpx.Response(204)
        if request.method == "GET" and "/state/" in str(request.url):
            key = str(request.url).rsplit("/", 1)[-1]
            if key not in self.state:
                return httpx.Response(204)
            return httpx.Response(200, json=self.state[key])
        return httpx.Response(404)


@pytest.fixture
def sidecar() -> FakeSidecar:
    return FakeSidecar()


@pytest.fixture
def store(sidecar) -> DaprSubmissionStore:
    client = httpx.AsyncClient(transport=httpx.MockTransport(sidecar.handler))
    return DaprSubmissionStore(DaprIO(client))


@pytest.mark.anyio
async def test_first_claim_wins_and_uses_first_write(sidecar, store):
    owner = await store.claim_fingerprint("vendor|inv-1|42.0", "tid-A")
    assert owner == "tid-A"
    insert = sidecar.requests[0]
    assert insert["options"]["concurrency"] == "first-write"
    assert "etag" not in insert


@pytest.mark.anyio
async def test_losing_racer_gets_winners_tracking_id(sidecar, store):
    """The 409 path: key already owned → the claimer reads back the owner
    instead of overwriting it (the old get-then-save adapter overwrote here)."""
    await store.claim_fingerprint("vendor|inv-1|42.0", "tid-A")
    owner = await store.claim_fingerprint("vendor|inv-1|42.0", "tid-B")
    assert owner == "tid-A"
    assert sidecar.state["fp::vendor|inv-1|42.0"] == "tid-A"  # not clobbered


@pytest.mark.anyio
async def test_idempotency_key_claims_share_semantics(sidecar, store):
    assert await store.claim_idempotency_key("key-1", "tid-A") == "tid-A"
    assert await store.claim_idempotency_key("key-1", "tid-B") == "tid-A"


@pytest.mark.anyio
async def test_conflict_shapes_are_recognized():
    """409, 412, and the 500-with-etag-text variant all count as conflicts."""
    for status, text in ((409, "conflict"), (412, "precondition"), (500, "possible etag mismatch")):
        def handler(request, status=status, text=text):
            return httpx.Response(status, text=text)

        dapr = DaprIO(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        assert await dapr.try_insert_state("statestore", "k", "v") is False
