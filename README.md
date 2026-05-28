# Webhook Handler

A reliable webhook receiver built with FastAPI. Supports GitHub and Jira Cloud webhooks, and is designed to be extended to any webhook source (Slack, Stripe, etc.) without touching the core processing pipeline.

Events are persisted to SQLite on arrival and processed asynchronously by a pool of workers, so nothing is lost if a handler fails or the process restarts.

**Key properties:**

- Immediate ACK — returns 200 after persisting to the queue but before processing, so the sender never times out
- Durable queue — events are stored in SQLite, not memory
- Deduplication — duplicate deliveries are rejected via the delivery ID primary key
- Retries with backoff — transient failures are retried automatically; permanent failures are logged
- Crash recovery — events stuck in "processing" are reset to "pending" on startup
- Automatic retention — completed events are pruned after 4 hours, failed after 4 days
- Provider-based architecture — each webhook source brings its own auth, header extraction, and event handlers; the queue, workers, and dispatcher are shared

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# Clone and configure
cp config.yaml.example config.yaml
# Edit config.yaml — point github.webhook_secret to your secret file

# Run (uses ./config.yaml by default)
make run

# Or specify a different config location
make run CONFIG=/etc/webhook/config.yaml

# The server listens on http://0.0.0.0:5000
# Point your GitHub webhook to https://your-host/webhooks/github
```

## Development

```bash
make install-dev   # install with dev dependencies
make test          # run tests
make lint          # ruff + mypy
make fmt           # auto-format
```

## Configuration

All configuration lives in `config.yaml` (see [`config.yaml.example`](config.yaml.example) for a ready-to-copy template):

```yaml
server:
  host: 0.0.0.0
  port: 5000
  worker_count: 4
  processing_timeout: 30
  max_payload_bytes: 26214400
  db_path: events.db

retry:
  max_attempts: 3
  backoff_base: 2.0

retention:
  success_hours: 4.0        # completed/skipped events
  failed_days: 4.0           # permanently failed events
  prune_interval_minutes: 60

github:
  webhook_secret: /run/secrets/github_webhook_secret

jira:
  webhook_secret: .jira_webhook_secret  # omit or leave empty to disable
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/webhooks/github` | POST | Receive GitHub webhook events |
| `/webhooks/jira` | POST | Receive Jira Cloud webhook events (when configured) |
| `/health` | GET | Queue depth, processed/failed counts |
| `/failed` | GET | Recent permanently failed events |

## Project structure

```
src/github_webhook/
├── providers/
│   ├── base.py        # WebhookProvider protocol
│   ├── github.py      # GitHub auth, headers, event handlers
│   └── jira.py        # Jira Cloud auth, payload extraction, event handlers
├── queue.py           # EventQueue protocol (swap backends here)
├── store.py           # SQLite implementation
├── handlers.py        # Event dispatcher (routes to provider handlers)
├── workers.py         # Async worker pool
├── errors.py          # RetriableError
├── config.py          # YAML config loader
└── app.py             # FastAPI app, wires everything together
```

## Setting up with GitHub

For a comprehensive overview of GitHub webhooks, see the [GitHub Webhooks Guide](https://www.magicbell.com/blog/github-webhooks-guide).

### 1. Expose your local server

GitHub needs a public HTTPS URL to deliver webhooks. For local development, use [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) to expose your local server — no account required.

Install cloudflared:

```bash
brew install cloudflared
```

Start the app and tunnel in two separate terminals:

```bash
# Terminal 1 — start the webhook server
make run

# Terminal 2 — start the tunnel (leave running while you develop)
make tunnel
```

Cloudflared will print a public URL:

```
https://some-random-words.trycloudflare.com
```

This URL stays stable as long as the tunnel is running. You can restart the app freely without losing it.

Verify the tunnel and server are reachable:

```bash
curl -s -o /dev/null -w "%{http_code}" https://your-tunnel-url.trycloudflare.com/health
# Should print: 200
```

### 2. Generate a webhook secret

Create a strong random secret and save it to a file:

```bash
openssl rand -hex 32 > .webhook_secret
```

Then point your `config.yaml` at it:

```yaml
github:
  webhook_secret: .webhook_secret
```

### 3. Configure the webhook on GitHub

1. Go to your repository on GitHub
2. Click **Settings** → **Webhooks** → **Add webhook**
3. Fill in the fields:
   - **Payload URL**: `https://some-random-words.trycloudflare.com/webhooks/github`
   - **Content type**: `application/json`
   - **Secret**: the same value from `.webhook_secret` (run `cat .webhook_secret` to copy it)
   - **SSL verification**: keep enabled
4. Under **Which events would you like to trigger this webhook?**, select **Send me everything** for local development (unhandled events are logged by the fallback handler and pruned automatically). For production, switch to **Let me select individual events** and check only the ones you need
5. Click **Add webhook**

### 4. Verify it works

GitHub sends a `ping` event immediately after creating the webhook. Check your server logs — you should see the event arrive:

```
INFO  Webhook queued: <delivery-id> github/ping
```

You can also go to **Settings → Webhooks → Recent Deliveries** on GitHub to inspect the delivery status and redeliver past events for testing.

## Setting up with Jira Cloud

For a comprehensive overview of Jira webhooks, see the [Hookdeck Guide to Jira Webhooks](https://hookdeck.com/webhooks/platforms/guide-to-jira-webhooks-features-and-best-practices).

### 1. Generate a webhook secret

```bash
openssl rand -hex 32 > .jira_webhook_secret
```

Update `jira.webhook_secret` in `config.yaml` (see [Configuration](#configuration) above) and restart the server — you should see Jira handlers registered in the logs.

### 2. Create the webhook in Jira

1. Go to your Jira Cloud instance: **Settings** (cog icon) → **System** → **WebHooks**
2. Click **Create a WebHook**
3. Fill in:
   - **Name**: descriptive name (e.g. "Local dev handler")
   - **URL**: `https://your-tunnel-url.trycloudflare.com/webhooks/jira`
   - **Secret**: the value from `.jira_webhook_secret` (run `cat .jira_webhook_secret`)
   - **Events**: select the events you care about (Issue created/updated/deleted, Comment created/updated/deleted)
4. Optionally add a JQL filter to limit which issues trigger webhooks
5. Click **Create**

### 3. Verify it works

Create or update an issue in a matching project. Your server logs should show:

```
INFO  Webhook queued: <delivery-id> jira/jira:issue_created
INFO  [PROJ] Issue PROJ-42 created by Jane Smith: Fix login timeout
```

### Handled Jira events

| Event | Description |
|---|---|
| `jira:issue_created` | New issue created |
| `jira:issue_updated` | Issue field changed (status, assignee, priority, etc.) |
| `jira:issue_deleted` | Issue deleted |
| `comment_created` | New comment on an issue |
| `comment_updated` | Comment edited |
| `comment_deleted` | Comment removed |
| `sprint_created` | New sprint created |
| `sprint_updated` | Sprint modified |
| `sprint_started` | Sprint started |
| `sprint_closed` | Sprint closed |
| `sprint_deleted` | Sprint deleted |
| `jira:version_released` | Version released |
| `jira:version_unreleased` | Version unreleased |
| `jira:version_deleted` | Version deleted |

Unhandled events are logged by the fallback handler and marked as `skipped`.

## Adding a new provider

1. Create `providers/your_provider.py` — implement `authenticate()`, `extract()`, and your event handlers
2. Add the provider to the `PROVIDERS` list in `app.py`

A route at `/webhooks/{provider_name}` is created automatically. Events land in the same SQLite queue and are routed to the correct handlers by the dispatcher.
