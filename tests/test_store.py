import pytest

from github_webhook.store import SqliteEventQueue


@pytest.fixture
async def store(tmp_path):
    q = SqliteEventQueue(db_path=str(tmp_path / "test.db"))
    await q.init()
    yield q
    await q.close()


async def test_insert_and_claim(store: SqliteEventQueue):
    inserted = await store.insert("d-1", "github", "push", b'{"ref": "refs/heads/main"}')
    assert inserted is True

    event = await store.claim()
    assert event is not None
    assert event["delivery_id"] == "d-1"
    assert event["provider"] == "github"
    assert event["status"] == "processing"


async def test_duplicate_rejected(store: SqliteEventQueue):
    await store.insert("d-1", "github", "push", b"{}")
    assert await store.insert("d-1", "github", "push", b"{}") is False


async def test_complete(store: SqliteEventQueue):
    await store.insert("d-1", "github", "push", b"{}")
    await store.claim()
    await store.complete("d-1")

    stats = await store.stats()
    assert stats.get("completed") == 1


async def test_permanent_failure(store: SqliteEventQueue):
    await store.insert("d-1", "github", "push", b"{}")
    await store.claim()
    await store.fail("d-1", "kaboom", retriable=False, attempts=1)

    stats = await store.stats()
    assert stats.get("failed") == 1

    failures = await store.recent_failures()
    assert len(failures) == 1
    assert failures[0]["error"] == "kaboom"


async def test_retriable_failure_schedules_retry(store: SqliteEventQueue):
    await store.insert("d-1", "github", "push", b"{}")
    await store.claim()
    await store.fail("d-1", "timeout", retriable=True, attempts=1)

    stats = await store.stats()
    assert stats.get("pending") == 1
    assert stats.get("failed", 0) == 0


async def test_retriable_exhausted_becomes_failed(store: SqliteEventQueue):
    await store.insert("d-1", "github", "push", b"{}")
    await store.claim()

    await store.fail("d-1", "timeout", retriable=True, attempts=1)
    assert (await store.stats()).get("pending") == 1

    store._conn.execute("UPDATE events SET retry_after = 0 WHERE delivery_id = 'd-1'")
    store._conn.commit()
    await store.claim()
    await store.fail("d-1", "timeout", retriable=True, attempts=2)

    store._conn.execute("UPDATE events SET retry_after = 0 WHERE delivery_id = 'd-1'")
    store._conn.commit()
    await store.claim()
    await store.fail("d-1", "timeout", retriable=True, attempts=3)

    stats = await store.stats()
    assert stats.get("failed") == 1


async def test_stale_processing_reset_on_init(tmp_path):
    db_path = str(tmp_path / "test.db")

    q1 = SqliteEventQueue(db_path=db_path)
    await q1.init()
    await q1.insert("d-1", "github", "push", b"{}")
    await q1.claim()
    stats = await q1.stats()
    assert stats.get("processing") == 1
    await q1.close()

    q2 = SqliteEventQueue(db_path=db_path)
    await q2.init()
    stats = await q2.stats()
    assert stats.get("processing", 0) == 0
    assert stats.get("pending") == 1
    await q2.close()


async def test_claim_returns_none_when_empty(store: SqliteEventQueue):
    event = await store.claim()
    assert event is None
