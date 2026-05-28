import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from github_webhook.app import app, queue
from github_webhook.config import cfg


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(cfg.github.read_secret(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
async def _init_store(tmp_path):
    cfg.github.webhook_secret = "test-secret"
    queue._db_path = str(tmp_path / "test.db")
    await queue.init()
    yield
    await queue.close()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


async def test_webhook_rejects_bad_signature(client: AsyncClient):
    resp = await client.post(
        "/webhooks/github",
        content=b"{}",
        headers={
            "x-hub-signature-256": "sha256=bad",
            "x-github-event": "push",
            "x-github-delivery": "d-1",
        },
    )
    assert resp.status_code == 401


async def test_webhook_accepts_valid_event(client: AsyncClient):
    body = json.dumps({"action": "opened"}).encode()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": _sign(body),
            "x-github-event": "issues",
            "x-github-delivery": "d-2",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "queued"
    assert data["provider"] == "github"


async def test_duplicate_delivery_ignored(client: AsyncClient):
    body = json.dumps({"ref": "refs/heads/main", "commits": []}).encode()
    headers = {
        "x-hub-signature-256": _sign(body),
        "x-github-event": "push",
        "x-github-delivery": "d-3",
    }
    r1 = await client.post("/webhooks/github", content=body, headers=headers)
    r2 = await client.post("/webhooks/github", content=body, headers=headers)
    assert r1.json()["status"] == "queued"
    assert r2.json()["status"] == "duplicate"
