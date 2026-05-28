"""Event dispatcher — routes events from the queue to provider handlers."""

import json
import logging
from typing import Any

from .providers.base import EventHandler

logger = logging.getLogger("webhook")


async def default_handler(payload: dict[str, Any]) -> None:
    """Fallback handler that logs unhandled events for observability."""
    action = payload.get("action", "-")
    repo = payload.get("repository", {}).get("full_name", "unknown")
    sender = payload.get("sender", {}).get("login", "unknown")
    logger.info(
        "Unhandled event: repo=%s sender=%s action=%s",
        repo,
        sender,
        action,
    )


class EventDispatcher:
    """Routes events to the correct handler based on provider + event type."""

    def __init__(self, fallback: EventHandler = default_handler) -> None:
        self._handlers: dict[str, dict[str, EventHandler]] = {}
        self._fallback = fallback

    def register(self, provider: str, handlers: dict[str, EventHandler]) -> None:
        """Register event handlers for a provider."""
        self._handlers[provider] = handlers
        logger.info("Registered %d handlers for provider '%s'", len(handlers), provider)

    async def dispatch(self, event: dict[str, Any]) -> bool:
        """Route event to the correct handler. Returns False if no specific handler exists."""
        provider = event.get("provider", "unknown")
        event_type = event["event_type"]
        payload = json.loads(event["payload"])

        provider_handlers = self._handlers.get(provider, {})
        handler = provider_handlers.get(event_type)

        if handler:
            await handler(payload)
            return True

        logger.info("No handler for %s/%s, using fallback", provider, event_type)
        await self._fallback(payload)
        return False
