"""FastAPI application — wires providers, queue, workers, and endpoints."""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import cfg
from .handlers import EventDispatcher
from .providers.github import GitHubProvider
from .providers.jira import JiraProvider
from .store import SqliteEventQueue
from .workers import worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook")

queue = SqliteEventQueue()
dispatcher = EventDispatcher()


def _build_providers() -> list:
    """Build the list of active providers based on config."""
    providers = [GitHubProvider()]
    if cfg.jira.webhook_secret:
        providers.append(JiraProvider())
    return providers


PROVIDERS = _build_providers()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start workers and pruner on startup, drain and stop on shutdown."""
    await queue.init()

    for p in PROVIDERS:
        dispatcher.register(p.name, p.event_handlers)

    shutdown = asyncio.Event()
    tasks = [
        asyncio.create_task(worker(i, queue, dispatcher, shutdown))
        for i in range(cfg.server.worker_count)
    ]
    tasks.append(asyncio.create_task(_pruner(shutdown)))
    logger.info("Started %d workers + pruner", cfg.server.worker_count)

    yield

    shutdown.set()
    queue.wake()
    _done, pending = await asyncio.wait(tasks, timeout=30)
    for t in pending:
        t.cancel()

    await queue.close()
    logger.info("Shutdown complete")


async def _pruner(shutdown: asyncio.Event) -> None:
    """Periodically delete old events based on retention config."""
    r = cfg.retention
    interval = r.prune_interval_minutes * 60
    success_max_age = r.success_hours * 3600
    failed_max_age = r.failed_days * 86400

    while not shutdown.is_set():
        try:
            deleted = await queue.prune(success_max_age, failed_max_age)
            if deleted:
                logger.info("Pruned %d old events", deleted)
        except (sqlite3.Error, OSError):
            logger.exception("Pruner error")

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
        except TimeoutError:
            pass


app = FastAPI(title="Webhook Handler", lifespan=lifespan)


def _register_provider_route(provider):
    """Create a POST /webhooks/{provider.name} route for each provider."""

    @app.post(f"/webhooks/{provider.name}", name=f"webhook_{provider.name}")
    async def handle_webhook(request: Request):
        """Authenticate, persist, and ACK an incoming webhook."""
        await provider.authenticate(request)
        event = await provider.extract(request)

        try:
            inserted = await queue.insert(
                event.delivery_id, provider.name, event.event_type, event.payload
            )
        except Exception as exc:
            logger.exception("Failed to store event %s", event.delivery_id)
            raise HTTPException(status_code=503, detail="Service unavailable") from exc

        status = "queued" if inserted else "duplicate"
        logger.info(
            "Webhook %s: %s %s/%s",
            status,
            event.delivery_id,
            provider.name,
            event.event_type,
        )

        return JSONResponse(
            content={
                "status": status,
                "delivery": event.delivery_id,
                "provider": provider.name,
                "event": event.event_type,
            },
            status_code=200,
        )


for _provider in PROVIDERS:
    _register_provider_route(_provider)


@app.get("/health")
async def health():
    """Return queue depth and event counts by status."""
    event_stats = await queue.stats()
    return {"status": "healthy", "events": event_stats}


@app.get("/failed")
async def list_failed():
    """Return recent permanently failed events for debugging."""
    failures = await queue.recent_failures()
    return {"count": len(failures), "events": failures}
