"""GitHub webhook provider — HMAC-SHA256 auth, header extraction, event handlers."""

import asyncio
import hashlib
import hmac
import logging
from typing import Any

from fastapi import HTTPException, Request

from ..config import cfg
from .base import EventHandler, IncomingEvent

logger = logging.getLogger("webhook")


async def process_pull_request(payload: dict[str, Any]) -> None:
    """Handle pull_request events (opened, synchronize, merged)."""
    action = payload.get("action")
    pr = payload.get("pull_request", {})

    logger.info("PR #%s %s: %s", pr.get("number"), action, pr.get("title"))

    if action in ("opened", "synchronize"):
        await asyncio.sleep(0.1)  # placeholder
        logger.info("Processed PR #%s", pr.get("number"))
    elif action == "closed" and pr.get("merged"):
        logger.info("PR #%s merged", pr.get("number"))


async def process_issue(payload: dict[str, Any]) -> None:
    """Handle issues events (opened, closed, etc.)."""
    action = payload.get("action")
    issue = payload.get("issue", {})
    logger.info("Issue #%s %s: %s", issue.get("number"), action, issue.get("title"))

    if action == "opened":
        await asyncio.sleep(0.1)  # placeholder


async def process_push(payload: dict[str, Any]) -> None:
    """Handle push events."""
    ref = payload.get("ref")
    commits = payload.get("commits", [])
    logger.info("Push to %s: %d commits", ref, len(commits))


HANDLERS: dict[str, EventHandler] = {
    "pull_request": process_pull_request,
    "issues": process_issue,
    "push": process_push,
}


# ─── Provider ────────────────────────────────────────────────────────────────

class GitHubProvider:
    """GitHub webhook source — HMAC-SHA256 auth, header-based metadata."""

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "github"

    @property
    def event_handlers(self) -> dict[str, EventHandler]:
        """Return the map of GitHub event types to handler functions."""
        return HANDLERS

    async def authenticate(self, request: Request) -> None:
        """Verify payload size and HMAC-SHA256 signature."""
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > cfg.server.max_payload_bytes:
                    raise HTTPException(status_code=413, detail="Payload too large")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length")

        body = await request.body()
        if len(body) > cfg.server.max_payload_bytes:
            raise HTTPException(status_code=413, detail="Payload too large")

        signature = request.headers.get("x-hub-signature-256", "")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing signature")

        secret = cfg.github.read_secret()
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("Invalid GitHub webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    async def extract(self, request: Request) -> IncomingEvent:
        """Extract delivery ID, event type, and payload from GitHub headers."""
        delivery_id = request.headers.get("x-github-delivery")
        if not delivery_id:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery header")

        event_type = request.headers.get("x-github-event")
        if not event_type:
            raise HTTPException(status_code=400, detail="Missing X-GitHub-Event header")

        body = await request.body()
        return IncomingEvent(
            delivery_id=delivery_id,
            event_type=event_type,
            payload=body,
        )
