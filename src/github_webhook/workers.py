"""Async worker pool that processes events from the queue."""

import asyncio
import logging

from .config import cfg
from .errors import RetriableError
from .handlers import EventDispatcher
from .queue import EventQueue

logger = logging.getLogger("webhook")


async def worker(
    worker_id: int,
    queue: EventQueue,
    dispatcher: EventDispatcher,
    shutdown: asyncio.Event,
) -> None:
    """Process events from the queue until shutdown is signalled."""
    logger.info("Worker %d started", worker_id)

    while not shutdown.is_set():
        event = await queue.claim()

        if event is None:
            await queue.wait_for_event(timeout=5.0)
            continue

        delivery_id = event["delivery_id"]
        attempts = event["attempts"] + 1
        logger.info("Worker %d processing: %s (attempt %d)", worker_id, delivery_id, attempts)

        try:
            timeout = cfg.server.processing_timeout
            handled = await asyncio.wait_for(dispatcher.dispatch(event), timeout=timeout)
            if handled:
                await queue.complete(delivery_id)
                logger.info("Worker %d completed: %s", worker_id, delivery_id)
            else:
                await queue.skip(delivery_id)

        except RetriableError as e:
            logger.warning("Retriable error on %s: %s", delivery_id, e)
            await queue.fail(delivery_id, str(e), retriable=True, attempts=attempts)

        except TimeoutError:
            logger.error("Timeout on %s (attempt %d)", delivery_id, attempts)
            await queue.fail(delivery_id, "timeout", retriable=True, attempts=attempts)

        except Exception as e:
            logger.error("Permanent failure on %s: %s", delivery_id, e)
            await queue.fail(delivery_id, str(e), retriable=False, attempts=attempts)

    logger.info("Worker %d stopped", worker_id)
