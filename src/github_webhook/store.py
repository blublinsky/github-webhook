"""SQLite-backed event queue implementation."""

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Any

from .config import cfg

logger = logging.getLogger("webhook")

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS events (
    delivery_id TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     BLOB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    retry_after REAL,
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_pending
    ON events(status, retry_after, created_at)
    WHERE status = 'pending';
"""


class SqliteEventQueue:
    """SQLite-backed implementation of ``EventQueue``.

    Survives process restarts, provides atomic claim/complete/fail operations,
    and handles deduplication via the delivery_id primary key.

    Thread safety: a ``threading.Lock`` serializes all connection access across
    the thread pool. WAL mode is enabled for durability.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or cfg.server.db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._notify = asyncio.Event()

    async def init(self) -> None:
        """Open the database, create schema, and reset stale events."""
        def _init() -> sqlite3.Connection:
            conn = sqlite3.connect(
                self._db_path, check_same_thread=False, isolation_level=None
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN")
            reset = conn.execute(
                "UPDATE events SET status = 'pending', updated_at = ? "
                "WHERE status = 'processing'",
                (time.time(),),
            ).rowcount
            conn.execute("COMMIT")
            if reset:
                logger.warning("Reset %d stale processing events from previous run", reset)
            return conn

        self._conn = await asyncio.to_thread(_init)
        logger.info("Event store ready: %s", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def insert(
        self, delivery_id: str, provider: str, event_type: str, payload: bytes
    ) -> bool:
        """Insert a new event. Returns False if delivery_id already exists (duplicate)."""
        now = time.time()

        def _insert() -> bool:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    self._conn.execute(
                        "INSERT INTO events "
                        "(delivery_id, provider, event_type, payload, status, attempts, "
                        " created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
                        (delivery_id, provider, event_type, payload, now, now),
                    )
                    self._conn.execute("COMMIT")
                    return True
                except sqlite3.IntegrityError:
                    self._conn.execute("ROLLBACK")
                    return False
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        inserted = await asyncio.to_thread(_insert)
        if inserted:
            self._notify.set()
        return inserted

    async def claim(self) -> dict[str, Any] | None:
        """Atomically claim the next pending event for processing."""
        now = time.time()

        def _claim() -> dict[str, Any] | None:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    cursor = self._conn.execute(
                        "UPDATE events SET status = 'processing', updated_at = ? "
                        "WHERE delivery_id = ("
                        "  SELECT delivery_id FROM events "
                        "  WHERE status = 'pending' "
                        "    AND (retry_after IS NULL OR retry_after <= ?) "
                        "  ORDER BY created_at LIMIT 1"
                        ") RETURNING *",
                        (now, now),
                    )
                    row = cursor.fetchone()
                    self._conn.execute("COMMIT")
                    return dict(row) if row else None
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        return await asyncio.to_thread(_claim)

    async def complete(self, delivery_id: str) -> None:
        """Mark an event as successfully processed."""
        def _complete() -> None:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    self._conn.execute(
                        "UPDATE events SET status = 'completed', updated_at = ? "
                        "WHERE delivery_id = ?",
                        (time.time(), delivery_id),
                    )
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        await asyncio.to_thread(_complete)

    async def skip(self, delivery_id: str) -> None:
        """Mark an event as skipped (no handler for its type)."""
        def _skip() -> None:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    self._conn.execute(
                        "UPDATE events SET status = 'skipped', updated_at = ? "
                        "WHERE delivery_id = ?",
                        (time.time(), delivery_id),
                    )
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        await asyncio.to_thread(_skip)

    async def fail(
        self, delivery_id: str, error: str, *, retriable: bool, attempts: int
    ) -> None:
        """Mark an event as failed, optionally scheduling a retry."""
        now = time.time()
        will_retry = retriable and attempts < cfg.retry.max_attempts

        def _fail() -> None:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    if will_retry:
                        backoff = cfg.retry.backoff_base**attempts
                        self._conn.execute(
                            "UPDATE events "
                            "SET status = 'pending', error = ?, attempts = ?, "
                            "    retry_after = ?, updated_at = ? "
                            "WHERE delivery_id = ?",
                            (error, attempts, now + backoff, now, delivery_id),
                        )
                    else:
                        self._conn.execute(
                            "UPDATE events "
                            "SET status = 'failed', error = ?, attempts = ?, updated_at = ? "
                            "WHERE delivery_id = ?",
                            (error, attempts, now, delivery_id),
                        )
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        await asyncio.to_thread(_fail)

        if will_retry:
            logger.info(
                "Event %s scheduled for retry (attempt %d/%d, backoff %.1fs)",
                delivery_id,
                attempts,
                cfg.retry.max_attempts,
                cfg.retry.backoff_base**attempts,
            )
            self._notify.set()

    async def wait_for_event(self, timeout: float = 5.0) -> None:
        """Block until a new event is inserted or timeout expires."""
        try:
            await asyncio.wait_for(self._notify.wait(), timeout=timeout)
        except TimeoutError:
            pass
        self._notify.clear()

    def wake(self) -> None:
        """Unblock any workers waiting in wait_for_event (used during shutdown)."""
        self._notify.set()

    async def prune(self, success_max_age: float, failed_max_age: float) -> int:
        """Delete old completed/skipped/failed events. Return count deleted."""
        now = time.time()
        success_cutoff = now - success_max_age
        failed_cutoff = now - failed_max_age

        def _prune() -> int:
            with self._lock:
                try:
                    self._conn.execute("BEGIN")
                    c1 = self._conn.execute(
                        "DELETE FROM events "
                        "WHERE status IN ('completed', 'skipped') AND updated_at < ?",
                        (success_cutoff,),
                    ).rowcount
                    c2 = self._conn.execute(
                        "DELETE FROM events WHERE status = 'failed' AND updated_at < ?",
                        (failed_cutoff,),
                    ).rowcount
                    self._conn.execute("COMMIT")
                    return c1 + c2
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise

        return await asyncio.to_thread(_prune)

    async def stats(self) -> dict[str, int]:
        """Return event counts grouped by status."""
        def _stats() -> dict[str, int]:
            with self._lock:
                cursor = self._conn.execute(
                    "SELECT status, COUNT(*) AS n FROM events GROUP BY status"
                )
                return {row["status"]: row["n"] for row in cursor}

        return await asyncio.to_thread(_stats)

    async def recent_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent permanently failed events."""
        def _failures() -> list[dict[str, Any]]:
            with self._lock:
                cursor = self._conn.execute(
                    "SELECT delivery_id, event_type, attempts, error, "
                    "       created_at, updated_at "
                    "FROM events WHERE status = 'failed' "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                )
                return [dict(row) for row in cursor]

        return await asyncio.to_thread(_failures)
