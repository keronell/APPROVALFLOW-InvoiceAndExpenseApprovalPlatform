"""Event-publishing abstraction (DIP).

The service depends on :class:`EventPublisher`, not on Dapr pub/sub. Tests use
:class:`InMemoryPublisher` to assert exactly-once publication.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class EventPublisher(Protocol):
    async def publish(self, topic: str, event: BaseModel, *, correlation_id: str) -> None: ...


class InMemoryPublisher:
    """Records published events for assertions."""

    def __init__(self) -> None:
        self.published: list[tuple[str, BaseModel, str]] = []

    async def publish(self, topic: str, event: BaseModel, *, correlation_id: str) -> None:
        self.published.append((topic, event, correlation_id))

    def topics(self) -> list[str]:
        return [topic for topic, _, _ in self.published]
