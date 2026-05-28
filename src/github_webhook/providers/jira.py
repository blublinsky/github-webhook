"""Jira Cloud webhook provider — HMAC-SHA256 auth, payload-based event routing."""

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import HTTPException, Request

from ..config import cfg
from .base import EventHandler, IncomingEvent

logger = logging.getLogger("webhook")


# ─── Handlers ─────────────────────────────────────────────────────────────────


async def process_issue_created(payload: dict[str, Any]) -> None:
    """Handle jira:issue_created events."""
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    key = issue.get("key", "?")
    summary = fields.get("summary", "")
    reporter = fields.get("reporter", {}).get("displayName", "unknown")
    project = fields.get("project", {}).get("key", "?")

    logger.info("[%s] Issue %s created by %s: %s", project, key, reporter, summary)


async def process_issue_updated(payload: dict[str, Any]) -> None:
    """Handle jira:issue_updated events."""
    issue = payload.get("issue", {})
    fields = issue.get("fields", {})
    key = issue.get("key", "?")
    project = fields.get("project", {}).get("key", "?")
    user = payload.get("user", {}).get("displayName", "unknown")

    changelog = payload.get("changelog", {})
    items = changelog.get("items", [])
    changes = [f"{c['field']}: {c.get('fromString', '')} → {c.get('toString', '')}"
               for c in items]

    logger.info("[%s] Issue %s updated by %s: %s", project, key, user, "; ".join(changes))


async def process_issue_deleted(payload: dict[str, Any]) -> None:
    """Handle jira:issue_deleted events."""
    issue = payload.get("issue", {})
    key = issue.get("key", "?")
    project = issue.get("fields", {}).get("project", {}).get("key", "?")
    user = payload.get("user", {}).get("displayName", "unknown")

    logger.info("[%s] Issue %s deleted by %s", project, key, user)


async def process_comment_created(payload: dict[str, Any]) -> None:
    """Handle comment_created events."""
    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    key = issue.get("key", "?")
    author = comment.get("author", {}).get("displayName", "unknown")
    body = comment.get("body", "")[:120]

    logger.info("Comment on %s by %s: %s", key, author, body)


async def process_comment_updated(payload: dict[str, Any]) -> None:
    """Handle comment_updated events."""
    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    key = issue.get("key", "?")
    author = comment.get("updateAuthor", {}).get("displayName", "unknown")
    body = comment.get("body", "")[:120]

    logger.info("Comment updated on %s by %s: %s", key, author, body)


async def process_comment_deleted(payload: dict[str, Any]) -> None:
    """Handle comment_deleted events."""
    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    key = issue.get("key", "?")
    author = comment.get("author", {}).get("displayName", "unknown")

    logger.info("Comment deleted on %s by %s", key, author)


async def process_sprint_started(payload: dict[str, Any]) -> None:
    """Handle sprint_started events."""
    sprint = payload.get("sprint", {})
    name = sprint.get("name", "?")
    board_id = sprint.get("originBoardId", "?")
    goal = sprint.get("goal", "")

    logger.info("Sprint started: %s (board %s)", name, board_id)
    if goal:
        logger.info("Sprint goal: %s", goal)


async def process_sprint_closed(payload: dict[str, Any]) -> None:
    """Handle sprint_closed events."""
    sprint = payload.get("sprint", {})
    name = sprint.get("name", "?")
    board_id = sprint.get("originBoardId", "?")

    logger.info("Sprint closed: %s (board %s)", name, board_id)


async def process_sprint_created(payload: dict[str, Any]) -> None:
    """Handle sprint_created events."""
    sprint = payload.get("sprint", {})
    name = sprint.get("name", "?")
    board_id = sprint.get("originBoardId", "?")

    logger.info("Sprint created: %s (board %s)", name, board_id)


async def process_sprint_updated(payload: dict[str, Any]) -> None:
    """Handle sprint_updated events."""
    sprint = payload.get("sprint", {})
    name = sprint.get("name", "?")
    board_id = sprint.get("originBoardId", "?")

    logger.info("Sprint updated: %s (board %s)", name, board_id)


async def process_sprint_deleted(payload: dict[str, Any]) -> None:
    """Handle sprint_deleted events."""
    sprint = payload.get("sprint", {})
    name = sprint.get("name", "?")

    logger.info("Sprint deleted: %s", name)


async def process_version_released(payload: dict[str, Any]) -> None:
    """Handle jira:version_released events."""
    version = payload.get("version", {})
    name = version.get("name", "?")
    project_key = version.get("projectId", "?")

    logger.info("Version released: %s (project %s)", name, project_key)


async def process_version_unreleased(payload: dict[str, Any]) -> None:
    """Handle jira:version_unreleased events."""
    version = payload.get("version", {})
    name = version.get("name", "?")

    logger.info("Version unreleased: %s", name)


async def process_version_deleted(payload: dict[str, Any]) -> None:
    """Handle jira:version_deleted events."""
    version = payload.get("version", {})
    name = version.get("name", "?")

    logger.info("Version deleted: %s", name)


HANDLERS: dict[str, EventHandler] = {
    "jira:issue_created": process_issue_created,
    "jira:issue_updated": process_issue_updated,
    "jira:issue_deleted": process_issue_deleted,
    "comment_created": process_comment_created,
    "comment_updated": process_comment_updated,
    "comment_deleted": process_comment_deleted,
    "sprint_created": process_sprint_created,
    "sprint_updated": process_sprint_updated,
    "sprint_deleted": process_sprint_deleted,
    "sprint_started": process_sprint_started,
    "sprint_closed": process_sprint_closed,
    "jira:version_released": process_version_released,
    "jira:version_unreleased": process_version_unreleased,
    "jira:version_deleted": process_version_deleted,
}


# ─── Provider ─────────────────────────────────────────────────────────────────


class JiraProvider:
    """Jira Cloud webhook source — HMAC-SHA256 auth via X-Hub-Signature."""

    @property
    def name(self) -> str:
        """Return the provider identifier."""
        return "jira"

    @property
    def event_handlers(self) -> dict[str, EventHandler]:
        """Return the map of Jira event types to handler functions."""
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

        signature = request.headers.get("x-hub-signature", "")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing signature")

        if not signature.startswith("sha256="):
            raise HTTPException(status_code=401, detail="Unsupported signature algorithm")

        secret = cfg.jira.read_secret()
        expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            logger.warning("Invalid Jira webhook signature")
            raise HTTPException(status_code=401, detail="Invalid signature")

    async def extract(self, request: Request) -> IncomingEvent:
        """Extract delivery ID, event type, and payload from a Jira webhook."""
        delivery_id = request.headers.get("x-atlassian-webhook-identifier")
        if not delivery_id:
            raise HTTPException(
                status_code=400, detail="Missing X-Atlassian-Webhook-Identifier header"
            )

        body = await request.body()

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        event_type = data.get("webhookEvent")
        if not event_type:
            raise HTTPException(status_code=400, detail="Missing webhookEvent in payload")

        return IncomingEvent(
            delivery_id=delivery_id,
            event_type=event_type,
            payload=body,
        )
