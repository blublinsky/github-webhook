"""Abstract event queue protocol — implement to swap storage backends."""

from __future__ import annotations

from typing import Any, Protocol


class EventQueue(Protocol):
    """Abstract interface for event storage backends.

    Implementations must be safe to call from async code.
    See ``SqliteEventQueue`` for the default implementation.
    """

    async def init(self) -> None:
        """Initialize the storage backend."""
        ...

    async def close(self) -> None:
        """Shut down the storage backend."""
        ...

    async def insert(
        self, delivery_id: str, provider: str, event_type: str, payload: bytes
    ) -> bool:
        """Store a new event. Return False if delivery_id is a duplicate."""
        ...

    async def claim(self) -> dict[str, Any] | None:
        """Atomically claim the next pending event for processing."""
        ...

    async def complete(self, delivery_id: str) -> None:
        """Mark an event as successfully processed."""
        ...

    async def skip(self, delivery_id: str) -> None:
        """Mark an event as skipped (no handler registered for its type)."""
        ...

    async def fail(
        self, delivery_id: str, error: str, *, retriable: bool, attempts: int
    ) -> None:
        """Mark an event as failed, optionally scheduling a retry."""
        ...

    async def wait_for_event(self, timeout: float = 5.0) -> None:
        """Block until a new event arrives or timeout expires."""
        ...

    def wake(self) -> None:
        """Unblock workers waiting in ``wait_for_event`` (used during shutdown)."""
        ...

    async def prune(self, success_max_age: float, failed_max_age: float) -> int:
        """Delete events older than the given ages (in seconds). Return count deleted."""
        ...

    async def stats(self) -> dict[str, int]:
        """Return event counts grouped by status."""
        ...

    async def recent_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent permanently failed events."""
        ...
