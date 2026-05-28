"""Base types and protocol for webhook providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class IncomingEvent:
    """Provider-agnostic representation of an incoming webhook event."""

    delivery_id: str
    event_type: str
    payload: bytes


class WebhookProvider(Protocol):
    """Interface that each webhook source must implement.

    To add a new provider (e.g. Jira, Slack):
      1. Create a module in providers/
      2. Implement this protocol
      3. Register it in app.py
    """

    @property
    def name(self) -> str:
        """Short identifier used in the URL path, e.g. ``/webhooks/github``."""
        ...

    @property
    def event_handlers(self) -> dict[str, EventHandler]:
        """Map of event type -> async handler function."""
        ...

    async def authenticate(self, request: Request) -> None:
        """Validate the request (signature, token, etc.).

        Raise ``fastapi.HTTPException`` on failure.
        """
        ...

    async def extract(self, request: Request) -> IncomingEvent:
        """Pull delivery ID, event type, and raw payload from the request."""
        ...
