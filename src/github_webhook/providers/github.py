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
    """Handle pull_request events across the full PR lifecycle."""
    action = payload.get("action")
    pr = payload.get("pull_request", {})
    number = pr.get("number")
    title = pr.get("title")
    user = pr.get("user", {}).get("login", "unknown")

    logger.info("PR #%s %s by %s: %s", number, action, user, title)

    if action == "opened":
        reviewers = [r.get("login") for r in pr.get("requested_reviewers", [])]
        if reviewers:
            logger.info("PR #%s reviewers requested: %s", number, ", ".join(reviewers))
    elif action == "closed":
        if pr.get("merged"):
            merged_by = pr.get("merged_by", {}).get("login", "unknown")
            logger.info("PR #%s merged by %s", number, merged_by)
        else:
            logger.info("PR #%s closed without merge", number)


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


async def process_issue_comment(payload: dict[str, Any]) -> None:
    """Handle issue_comment events (comments on issues and PRs)."""
    action = payload.get("action")
    comment = payload.get("comment", {})
    issue = payload.get("issue", {})
    number = issue.get("number")
    commenter = comment.get("user", {}).get("login", "unknown")
    is_pr = "pull_request" in issue

    kind = "PR" if is_pr else "Issue"
    logger.info(
        "%s #%s comment %s by %s: %s",
        kind,
        number,
        action,
        commenter,
        comment.get("body", "")[:120],
    )


async def process_pull_request_review(payload: dict[str, Any]) -> None:
    """Handle pull_request_review events (submitted, dismissed, edited)."""
    action = payload.get("action")
    review = payload.get("review", {})
    pr = payload.get("pull_request", {})
    number = pr.get("number")
    reviewer = review.get("user", {}).get("login", "unknown")
    state = review.get("state", "unknown")

    logger.info(
        "PR #%s review %s by %s: %s",
        number,
        action,
        reviewer,
        state,
    )

    if action == "submitted":
        if state == "approved":
            logger.info("PR #%s approved by %s", number, reviewer)
        elif state == "changes_requested":
            logger.info("PR #%s changes requested by %s", number, reviewer)


async def process_pull_request_review_comment(payload: dict[str, Any]) -> None:
    """Handle pull_request_review_comment events (inline code comments)."""
    action = payload.get("action")
    comment = payload.get("comment", {})
    pr = payload.get("pull_request", {})
    number = pr.get("number")
    commenter = comment.get("user", {}).get("login", "unknown")
    path = comment.get("path", "")

    logger.info(
        "PR #%s inline comment %s by %s on %s: %s",
        number,
        action,
        commenter,
        path,
        comment.get("body", "")[:120],
    )


HANDLERS: dict[str, EventHandler] = {
    "pull_request": process_pull_request,
    "pull_request_review": process_pull_request_review,
    "pull_request_review_comment": process_pull_request_review_comment,
    "issue_comment": process_issue_comment,
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
