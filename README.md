# llm-proxy

llm-proxy is an HTTP-based API built with FastAPI.

It uses uv for package management and to run the service. Ruff is used for formatting and linting.

It detects which of the following API keys are configured:

- PERPLEXITY_API_KEY
- OPENAI_API_KEY
- OPENROUTER_API_KEY
- GEMINI_API_KEY (or GOOGLE_API_KEY)
- CEREBRAS_API_KEY
- ANTHROPIC_API_KEY
- DEEPSEEK_API_KEY

and enables the corresponding provider functionality.

llm-proxy implements the OpenAI chat and embeddings APIs

(see: https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml)

and uses OpenAI-compatible endpoints. It also provides Ollama and llama.cpp compatibility
endpoints (`/api/tags`, `/api/show`, `/version`, `/props`) so clients like Open WebUI can
discover and use models without extra configuration.

It can also sit in front of non-LLM HTTP APIs: [Brave Search](#brave-search-pass-through)
and [any REST API you name](#generic-rest-pass-through). The proxy holds their keys,
traces every call and, for the generic route, can cache `GET` responses for a set time.

You select the model/provider via the `model` field
(Perplexity, OpenAI, OpenRouter, Google Gemini, Cerebras, Anthropic, DeepSeek).

Model selection supports a provider prefix, e.g. `openai/gpt-4o-mini` or
`openrouter/openai/gpt-4o-mini`. The older colon form (`openai:gpt-4o-mini`) still
works but is deprecated and logs a warning.
If no prefix is provided, `LLM_PROXY_DEFAULT_PROVIDER` is used (defaults to `openai`).
If the `model` field is omitted or empty, `LLM_PROXY_DEFAULT_MODEL` is used (defaults to `deepseek/deepseek-v4-pro`).

Each provider needs an API key. Base URLs have built-in defaults and can be overridden via:
- OPENAI_API_KEY / OPENAI_BASE_URL
- OPENROUTER_API_KEY / OPENROUTER_BASE_URL
- PERPLEXITY_API_KEY / PERPLEXITY_BASE_URL
- GEMINI_API_KEY (or GOOGLE_API_KEY)
- CEREBRAS_API_KEY / CEREBRAS_BASE_URL
- ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
- DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL

The pass-throughs are not LLM providers and have their own variables (see their sections below):
- BRAVE_API_KEY / BRAVE_BASE_URL / BRAVE_TIMEOUT_S / BRAVE_RPS (Brave Search)
- LLM_PROXY_REST_UPSTREAMS plus `<NAME>_BASE_URL` etc. per upstream (generic REST pass-through)

Gemini requests are sent via the official `google-genai` Python SDK rather than OpenAI-compatible HTTP calls.
`GEMINI_BASE_URL` is not used by the SDK path.

Optional auth overrides are available per provider:
- <PROVIDER>_AUTH_HEADER (default: Authorization)
- <PROVIDER>_AUTH_PREFIX (default: Bearer)
- <PROVIDER>_AUTH_QUERY (send API key as a query param instead)

Inbound authentication is not implemented; this is intended for internal use. Accordingly:

> Do not expose this to the internet. Inbound authentication is
> not implemented and the default bind address is `0.0.0.0`. Anyone who can reach
> the server can spend your upstream API credits and read the request/response
> traces stored in the database. Run it only on a trusted network (e.g. `localhost`
> or a private/VPN interface), or place an authenticating reverse proxy in front.

## Getting started

Prerequisites:

- [uv](https://docs.astral.sh/uv/). It installs the pinned Python (3.14.1, see `.python-version`) and the dependencies the first time you run anything, so there is no separate install step.
- An API key for at least one provider.
- Optional: Postgres (SQLite is the default), `psql` or `sqlite3` for the `make stats-*` targets, and Node.js with `npm` for the trace viewer.

Configuration is by environment variables only. The proxy does not read a `.env` file, so export them in your shell (or use the systemd `EnvironmentFile` described below).

```sh
git clone https://github.com/algrt-hm/llm-proxy.git
cd llm-proxy
export OPENAI_API_KEY=sk-...        # any of the provider keys listed above
make run-sqlite                     # port 6969, traces in ./llmproxy.db
```

Then, from another terminal:

```sh
curl http://localhost:6969/health
curl http://localhost:6969/status   # which provider keys were accepted
curl http://localhost:6969/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"openai/gpt-4o-mini","messages":[{"role":"user","content":"hi"}]}'
```

Interactive API docs are at `http://localhost:6969/docs`. Any OpenAI-compatible client works with `http://localhost:6969/v1` as its base URL and any placeholder API key.

To use Postgres instead, create an empty database (the proxy creates its own tables at startup) and point `LLM_PROXY_DATABASE_URL` at it:

```sh
createdb llm-proxy
export LLM_PROXY_DATABASE_URL=postgresql://postgres@localhost/llm-proxy
make run
```

`make run` sets `LLM_PROXY_RUN_ALT_POSTGRES=1`, which adds a startup check: the database URL must be valid and **every** provider's API key must be set. With a key missing it asks whether to continue when run in a terminal, and exits when run non-interactively (e.g. under systemd). Use `make run-sqlite`, or leave that variable unset, to start with only some providers configured.

## Runtime environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROXY_HOST` | `0.0.0.0` | Server bind address |
| `LLM_PROXY_PORT` | `8000` | Server port |
| `LLM_PROXY_RELOAD` | off | Enable dev auto-reload (`1`, `true`, `yes`, `y` or `on`) |
| `LLM_PROXY_DEFAULT_PROVIDER` | `openai` | Provider used when model has no prefix |
| `LLM_PROXY_DEFAULT_MODEL` | `deepseek/deepseek-v4-pro` | Model (with optional provider prefix) used when `model` field is omitted or empty |
| `LLM_PROXY_TIMEOUT_S` | `900` | HTTP request timeout (seconds) |
| `LLM_PROXY_DB_URL` | `sqlite+aiosqlite:///./llmproxy.db` | SQLAlchemy database URL (SQLite 3.35+ and Postgres supported, e.g. `postgresql+asyncpg://user:pass@localhost/llmproxy`) |
| `LLM_PROXY_RUN_ALT_POSTGRES` | off | Run the strict startup check (valid `LLM_PROXY_DB_URL` and every provider key set; see Getting started). Set by `make run` |
| `LLM_PROXY_MAX_RETRIES` | `2` | Max retry attempts on transient upstream errors (429, 502, 503, 504) |
| `LLM_PROXY_RETRY_BASE_DELAY_S` | `1.0` | Base backoff delay (seconds); baseline delay is `base * 2^attempt` (capped at 30s inside shared retry helper) |
| `LLM_PROXY_MODELS_CACHE` | `./llmproxy_models.json` | Path to cached model lists (re-fetches after 24h) |
| `ANTHROPIC_ADMIN_API_KEY` | _(unset)_ | Admin API key (`sk-ant-admin01-…`), tried only if `ANTHROPIC_API_KEY` is refused by the Admin API. Neither accepted → the static table below is used |
| `LLM_PROXY_ANTHROPIC_LIMITS_CACHE` | `./llmproxy_anthropic_limits.json` | Path to cached Anthropic rate limits (re-fetches after 24h) |
| `LLM_PROXY_DATABASE_URL` | — | Postgres connection string used by the web frontend and `make run`/`make stats-postgres` (plain `postgresql://`, e.g. `postgresql://postgres@myhost/llm-proxy`) |
| `LLM_PROXY_ANTHROPIC_RPM` | `50` | Anthropic client-side rate limiter: requests/minute (all models) |
| `LLM_PROXY_ANTHROPIC_TPM` | `30000` | Anthropic limiter: input tokens/minute for model families without a specific override |
| `LLM_PROXY_ANTHROPIC_OPUS_TPM` | `500000` | Anthropic limiter: input tokens/minute for `claude-opus*` models |
| `LLM_PROXY_ANTHROPIC_SONNET_TPM` | `30000` | Anthropic limiter: input tokens/minute for `claude-sonnet*` models |
| `LLM_PROXY_ANTHROPIC_HAIKU_TPM` | `50000` | Anthropic limiter: input tokens/minute for `claude-haiku*` models |
| `LLM_PROXY_ANTHROPIC_FABLE_TPM` | `1500000` | Anthropic limiter: input tokens/minute for `claude-fable*` models |

### Live Anthropic rate limits

The five `LLM_PROXY_ANTHROPIC_*_TPM` values above are a **fallback**. At startup
the proxy tries to read the account's real limits from
[`GET /v1/organizations/rate_limits`](https://platform.claude.com/docs/en/manage-claude/rate-limits-api)
and uses those instead, so the numbers can't drift out of date.

Keys are tried in order, and whether one works is settled by asking rather than
by inspecting its prefix:

1. `ANTHROPIC_API_KEY` — the variable Anthropic's own SDKs read for Admin API
   calls. If your inference key is admin-capable, nothing else needs setting.
2. `ANTHROPIC_ADMIN_API_KEY` — tried only if the first is refused (401/403).

Only an auth refusal moves on to the next key; a network failure is not
key-specific, so it stops there. Shape proves nothing about admin capability —
a Claude Enterprise admin key is `sk-ant-api01-…`, and an organization-scoped
personal or service-account key is accepted too — which is why no prefix check
is applied.

The response enumerates every model id and alias in each rate limit group, so
lookup is an exact match — no prefix guessing. Models the API doesn't mention
still resolve through the static prefix table.

Two caveats worth knowing before you set it up:

- This is an **Admin API** endpoint. It needs an Admin API key
  (`sk-ant-admin01-…`) or an organization-scoped key; an ordinary `sk-ant-api…`
  inference key returns 401, and the Admin API is unavailable to individual
  (non-organization) accounts.
- Only **org-level** limits are read. Workspace overrides live on a separate
  endpoint and are not applied.

Anything short of success (no key, 401/403, network failure, unparseable body)
logs a warning and leaves the static table in charge — startup never fails over
this. `GET /status` reports which is in effect:

```json
"anthropic_rate_limits": { "source": "admin_api", "models": 7 }
```

`source` is `static_table` with `models: 0` when live limits aren't in use.

## Brave Search pass-through

`GET` and `POST /brave/res/v1/{path}` forward to the [Brave Search API](https://api-dashboard.search.brave.com/api-reference)
(`https://api.search.brave.com/res/v1/{path}`, override with `BRAVE_BASE_URL`),
adding `X-Subscription-Token` from `BRAVE_API_KEY`. It mirrors Brave's own URL
layout, so a client changes only its base URL to `http://<proxy>/brave/res/v1`
and sends no key. Every path, query parameter and POST body is passed through unchanged, e.g.:

```sh
curl "http://localhost:6969/brave/res/v1/llm/context?q=how+deep+is+the+mediterranean"
```

- Brave is not an LLM provider, so it is not in the provider list, not
  validated at startup, and not required by the missing-key preflight.
  `GET /status` reports `"brave": {"configured": true|false}`.
- Without `BRAVE_API_KEY`, the route returns `503`. A path that isn't a plain
  lowercase `a/b` path of at most 128 characters (e.g. one containing `..`) returns
  `400` and is never forwarded; it is traced with model `invalid-path`.
- Only `Accept`, `Api-Version`, `Cache-Control`, `Content-Type`, `User-Agent` and
  `X-Loc-*` request headers are forwarded. A client-supplied `X-Subscription-Token`
  (header or query param) is dropped. Brave's `X-RateLimit-*` and `Retry-After`
  response headers are passed back. The response body and content type are
  returned exactly as Brave sent them; streamed responses are buffered and
  delivered in one piece.
- Each call is recorded in `traces` with provider `brave` and model set to the
  endpoint path (`llm/context`); the request payload is `{"path", "params"}`
  (plus `"method"` and `"body"` for POST) and never contains the key.
- Each upstream attempt times out after `BRAVE_TIMEOUT_S` seconds (default 30),
  not the proxy-wide `LLM_PROXY_TIMEOUT_S`.
- Retries follow the same rules as the LLM providers (below). Calls are also
  paced at `BRAVE_RPS` per second (default 1, the free plan; `0` disables
  pacing), and a `429` holds queued searches until Brave's window resets
  (`Retry-After`, else the per-second part of `X-RateLimit-Reset`).
- Responses are **never** served from the response cache: that cache has no TTL,
  and search results should change over time.

## Generic REST pass-through

Any other HTTP API can be put behind the proxy without code changes, to get the
same key handling, tracing, retries and pacing, plus an optional TTL cache.
List the upstreams in `LLM_PROXY_REST_UPSTREAMS` (comma-separated) and give each
a base URL; `/rest/<name>/<path>` then forwards to `<NAME>_BASE_URL/<path>` with
the query string and body unchanged. `GET`, `POST`, `PUT`, `PATCH` and `DELETE`
are accepted.

For example, the [TfL API](https://api.tfl.gov.uk), which takes its key as the
`app_key` query parameter:

```sh
LLM_PROXY_REST_UPSTREAMS=tfl
TFL_BASE_URL=https://api.tfl.gov.uk
TFL_API_KEY=...
TFL_AUTH_QUERY=app_key
TFL_CACHE_TTL_S=60
```

```sh
curl "http://localhost:6969/rest/tfl/Line/Mode/tube/Status"
```

Per-upstream settings (`<NAME>` is the upper-cased name):

| Variable | Default | Meaning |
|---|---|---|
| `<NAME>_BASE_URL` | required | Where `/rest/<name>/` points. |
| `<NAME>_API_KEY` | none | Credential the proxy adds. Omit for a public API. |
| `<NAME>_AUTH_HEADER` / `<NAME>_AUTH_PREFIX` | `Authorization` / `Bearer` | Header the key is sent in (set the prefix to empty for a bare key). |
| `<NAME>_AUTH_QUERY` | none | Send the key as this query parameter instead of a header. |
| `<NAME>_CACHE_TTL_S` | `0` (off) | Serve a repeated `GET` from the trace DB for this many seconds. |
| `<NAME>_RPS` | `0` (unpaced) | Maximum upstream calls per second. |
| `<NAME>_TIMEOUT_S` | `30` | Timeout per upstream attempt. |
| `<NAME>_FORWARD_HEADERS` | none | Extra request headers to forward (comma-separated), e.g. an API-version header. |

- A name is lowercase `[a-z][a-z0-9_]{0,31}` and cannot be an LLM provider's or
  `brave`. `GET /status` lists each upstream under `"rest"` (never its key) and
  says why a misconfigured one is refused; a request to one returns `503`, and
  to an unlisted name `404`.
- Clients send no credentials. Only `Accept`, `Accept-Language`, `Content-Type`,
  `User-Agent` and the `<NAME>_FORWARD_HEADERS` are forwarded; `Authorization`,
  `Cookie` and the upstream's own key header or query parameter are always
  dropped from the client's request.
- A path with a `.` or `..` segment returns `400` and is never forwarded. The
  path is decoded and re-encoded on the way through, so an encoded slash (`%2F`)
  inside a segment reaches the upstream as a plain `/`.
- The response status, body and content type are returned as the upstream sent
  them, with its headers except hop-by-hop ones, `Set-Cookie` and `X-Cache`.
  Redirects are returned, not followed.
- Each call is recorded in `traces` with provider = the upstream name and model
  = the path (cut to 256 characters); the request payload is
  `{"method", "path", "params"}` plus `"headers"` and `"body"` when present,
  and never contains the key. Requests to an unlisted or misconfigured
  upstream are not traced.
- **Caching** applies to `GET` responses with a 2xx status, keyed on path, query
  and forwarded headers (except `User-Agent`). A hit returns `x-cache: hit`, an
  `Age` header and the original `x-request-id`, and is logged in `cache_hits`;
  a cacheable miss returns `x-cache: miss`. Send `Cache-Control: no-cache` to
  skip the lookup; the fresh answer then replaces the cached one. JSON is
  served re-serialised as `application/json`, other UTF-8 text byte-for-byte
  with its content type; binary and empty responses are not cached. A hit
  carries none of the upstream's other headers.
- Retries follow the rules below for `GET`, `PUT` and `DELETE`. `POST` and
  `PATCH` are retried only after a `429`, since repeating one after a dropped
  connection or a `5xx` could do the work twice. A `429` also holds queued
  calls to that upstream until its `Retry-After` has passed.

## Retries

- Retryable upstream conditions: HTTP status `429/502/503/504` and connection errors. Timeout exceptions are **not retried** (fails immediately with 504).
- Default retry count: `LLM_PROXY_MAX_RETRIES=2` (up to 3 total attempts).
- Baseline delay is exponential backoff: `LLM_PROXY_RETRY_BASE_DELAY_S * 2^attempt`.
- HTTP providers: when upstream sends a `Retry-After` header it is respected as-is (plus jitter, uncapped — the server knows its own rate-limit window); otherwise exponential backoff with jitter is used.
- Gemini SDK (non-streaming chat + embeddings): retries are exception-driven and now parse retry hints from SDK error text (for example `Please retry in 53s` or `retryDelay: '53s'`). When present, the proxy waits at least that hinted duration before retrying.
- Gemini streaming requests are not retried.

## Response caching

- Non-streaming `POST /v1/chat/completions` and `POST /v1/embeddings` requests are cached by `(provider, model, request payload)` using the `traces` table.
- Embeddings are always eligible for caching (never streaming) and benefit especially since they are deterministic.
- Cache lookups are enabled by default.
- Disable cache lookup for a specific request with `cache: false` in the request body.
- Invalid `cache` values return `400` (expected boolean-like value).
- Streaming responses are not served from cache (the `stream` flag is part of the cache key, so streaming and non-streaming requests never collide).
- Degenerate chat completions (all choices have null or empty content) are not served from cache, preventing cache poisoning from flaky provider responses.
- Cache hits (both response and idempotency) are logged to a `cache_hits` table for observability.
- There is deliberately no TTL: identical requests are served from cache indefinitely (use `cache: false` to bypass).

## Idempotency

- Send an `Idempotency-Key` header (max 256 chars) on `POST /v1/chat/completions` (non-streaming) or `POST /v1/embeddings` for retry safety: repeating a request with the same key returns the stored response (`x-idempotency: cached` header) instead of hitting the provider again.
- Keys are scoped to `(key, provider, model)`, and only successful (2xx) responses are replayed.
- Reusing a key with a **different request payload** returns `409 Conflict` (Stripe-style) rather than silently serving the previous response. The check compares the request fingerprint (`cache_key`) stored on the trace; traces recorded before fingerprints existed are served without the check.
- Anthropic fingerprints are computed from the payload as sent upstream (i.e. including the injected prompt-caching `cache_control` breakpoints), matching what is recorded in traces.

## Streaming

- Streaming chat completions (`stream: true`) are proxied through as raw SSE from upstream to the client with no transformation overhead.
- As chunks pass through, the proxy incrementally parses SSE data lines to extract the assistant text and usage metadata — no buffering of the full raw response in memory.
- After the stream completes, a structured `chat.completion` trace (same shape as non-streaming responses, including token usage) is recorded to the database.
- Gemini streaming uses the Google GenAI SDK and builds OpenAI-compatible SSE chunks on the fly.
- Trace writes are shielded from client disconnects via `asyncio.shield`, so a client dropping mid-stream does not lose the trace.

## Formatting, linting and tests

- `make format` (runs `uv run ruff check --fix .` then `uv run ruff format .`)
- `make lint` (runs `uv run ruff check .` without applying fixes)
- `make test` (runs `uv run pytest tests/`)

## Running locally

- `make run` (port `6969`, Postgres DB via `LLM_PROXY_DATABASE_URL`, runs at low CPU priority via `nice -n20`)
- `make run-sqlite` (port `6969`, SQLite DB, runs at low CPU priority via `nice -n20`)
- `uv run python main.py --require-db-url` (fail fast if `LLM_PROXY_DB_URL` is unset)

The server binds to `0.0.0.0` by default, so it is accessible to other machines on the LAN.
For OpenAI-compatible clients on the same network, use `http://<LAN_IP>:6969/v1` as the base URL (no API key required).
Clients that probe for Ollama or llama.cpp endpoints (e.g. Open WebUI) will also work — the proxy responds to `/api/tags`, `/api/show`, `/api/v1/models`, `/version`, and `/props`.

## Running as a systemd service

The proxy runs as a systemd **user** service (no root required), configured for the Postgres backend on port `6969`:

- Unit file: `~/.config/systemd/user/llm-proxy.service`
- Environment file: `~/.config/llm-proxy/env` (mode `600`; provider API keys plus `LLM_PROXY_DB_URL`, `LLM_PROXY_PORT`, `LLM_PROXY_RUN_ALT_POSTGRES`, and, for the pass-throughs, `BRAVE_API_KEY` and `LLM_PROXY_REST_UPSTREAMS` with each upstream's `<NAME>_*` settings; restart the service after editing it)
- Lingering is enabled (`loginctl enable-linger`), so the service starts at boot without a login

The unit file (adjust the two absolute paths to your checkout and your `uv`):

```ini
[Unit]
Description=LLM proxy (FastAPI on port 6969, Postgres backend)
After=network-online.target
StartLimitIntervalSec=0

[Service]
WorkingDirectory=/home/mike/repos/llm-proxy
EnvironmentFile=%h/.config/llm-proxy/env
ExecStart=/home/mike/.local/bin/uv run python main.py
Nice=19
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

The environment file is plain `KEY=value` lines, for example:

```sh
LLM_PROXY_PORT=6969
LLM_PROXY_DB_URL=postgresql+asyncpg://postgres@dbhost/llm-proxy
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
BRAVE_API_KEY=...
LLM_PROXY_REST_UPSTREAMS=tfl
TFL_BASE_URL=https://api.tfl.gov.uk
TFL_API_KEY=...
TFL_AUTH_QUERY=app_key
TFL_CACHE_TTL_S=60
```

Note that the service reads `LLM_PROXY_DB_URL` directly (SQLAlchemy form, `postgresql+asyncpg://`); only `make run` derives it from `LLM_PROXY_DATABASE_URL`.

First-time install:

```bash
mkdir -p ~/.config/systemd/user ~/.config/llm-proxy
# write the unit file and the env file shown above, then:
chmod 600 ~/.config/llm-proxy/env
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now llm-proxy
```

To deploy a new version: `git pull` in the checkout, then `systemctl --user restart llm-proxy`.

Useful commands:

```bash
systemctl --user status llm-proxy     # check state
systemctl --user restart llm-proxy    # restart (e.g. after a code change or key rotation)
systemctl --user stop llm-proxy       # stop
systemctl --user start llm-proxy      # start
journalctl --user -u llm-proxy -f     # follow logs (replaces llm-proxy-alt.log)
journalctl --user -u llm-proxy -n 100 # last 100 log lines
```

After editing the unit file, run `systemctl --user daemon-reload` before restarting.
After rotating an API key, edit `~/.config/llm-proxy/env` and restart the service.

Note: when the service is running it holds port `6969`, so stop it first (`systemctl --user stop llm-proxy`) before starting a foreground instance with `make run`.

## Provider checks

- `make ping-providers` (proxy chat smoke test; uses credits). Needs a running proxy and one `LLM_PROXY_TEST_MODEL_<PROVIDER>` variable per provider to test, e.g. `LLM_PROXY_TEST_MODEL_OPENAI=gpt-5-nano make ping-providers`.
- `make ping-providers-all` (same, with the cheap default models set in the makefile: `CHEAP_<PROVIDER>_MODEL`)
- Optional for both, and for `make validate-keys`: `LLM_PROXY_TEST_PROVIDERS` (comma-separated subset), `LLM_PROXY_TEST_TIMEOUT_S` (default 60 for the ping, 30 for validation). `LLM_PROXY_TEST_FORCE_ALL=1` runs a provider even when its key is not set locally.
- Ping targets use `LLM_PROXY_BASE_URL=http://localhost:6969` and the ping script normalizes local base URLs to port `6969`.
- `make validate-keys` (direct upstream key validation; free/read-only endpoints)
- On server startup, llm-proxy runs the same free/read-only key validation logic as `make validate-keys` and caches results in-memory for the lifetime of the process.

## Web frontend

A Next.js trace viewer runs on port `3123` and queries Postgres directly (no FastAPI dependency).

The frontend reads `LLM_PROXY_DATABASE_URL` for its Postgres connection string (plain `postgresql://`, not the `+asyncpg` variant used by the Python backend).

```bash
export LLM_PROXY_DATABASE_URL="postgresql://postgres@myhost/llm-proxy"
make install-web   # once
make dev-web
```

It needs Postgres: it does not read the SQLite database.

- `make install-web` — install frontend dependencies
- `make dev-web` — start dev server on port `3123`
- `make build-web` — production build
- `make lint-web` — lint frontend code

Pages: Dashboard (`/`), Traces (`/traces` — split-pane with inline detail), Latency (`/latency` — one scatter plot per provider/model, or per endpoint for a REST upstream (its paths are grouped by first segment, so every TfL `Journey/...` request is one plot): a dot per request over time, with lines at the 50th, 75th, 90th, 95th and 99th percentiles, computed in Postgres; log or linear scale, click a dot to open its trace), Cache Hits (`/cache-hits` — split-pane with inline detail).

## Database & observability

Every request and response is recorded in full in the `traces` table. Database access goes through SQLAlchemy, so the backend can be SQLite (the default) or Postgres; set it with `LLM_PROXY_DB_URL`.

- `make stats-sqlite` (per-model trace counts, token usage, cache hit rates)
- `make stats-postgres` (same stats from `LLM_PROXY_DATABASE_URL`)
- `make stats-postgres` requires `LLM_PROXY_DATABASE_URL` to be set and `psql` installed/on `PATH`

## File map
```
.
├─ .gitignore               # Git ignore rules
├─ .python-version          # Python version pin
├─ CLAUDE.md                # Agent notes (gitignored, local only)
├─ makefile                 # Developer tasks (help/run/run-sqlite/stop/ping/validate/format/lint/test/stats-sqlite/stats-postgres/install-web/dev-web/build-web/lint-web)
├─ main.py                  # Uvicorn entrypoint for llmproxy.app:app
├─ pyproject.toml           # Dependencies + project metadata
├─ README.md                # Usage + env configuration
├─ uv.lock                  # Resolved dependency lockfile (gitignored, local only)
├─ scripts/
│  ├─ ping_providers.py     # Smoke tests against local proxy
│  ├─ stop.sh               # Kill running llm-proxy instance by port
│  └─ validate_keys.py      # API key validation (free, no credits)
├─ tests/
│  ├─ __init__.py
│  ├─ test_anthropic_limits.py # Live Anthropic rate limits: Admin API parsing, pagination, key fallback, caching
│  ├─ test_brave.py         # Brave pass-through: forwarding, validation, tracing, retries, pacing, timeouts
│  ├─ test_gemini.py        # Gemini config builder tests (thinking/reasoning)
│  ├─ test_ratelimit.py     # Rate limiter tests (RPM/TPM, per-model limits, prefix matching, eviction, retry integration)
│  ├─ test_retry.py         # Retry delay parsing tests (Retry-After + Gemini SDK retry hints)
│  ├─ test_response_cache.py # Response cache, idempotency cross-endpoint rejection, empty input validation, degenerate response rejection tests
│  ├─ test_rest.py          # Generic REST pass-through: forwarding, key injection, TTL cache, retries by method, status
│  ├─ test_payload_passthrough.py  # Schema extra-field passthrough + URL builder tests
│  └─ test_main_preflight.py # Preflight checks: --require-db-url, DB URL validation, missing API keys
├─ llmproxy/
│  ├─ __init__.py           # Package version
│  ├─ anthropic_limits.py   # Reads live Anthropic rate limits from the Admin API + disk caching
│  ├─ app.py                # FastAPI app, routing, proxy logic (chat + embeddings), compatibility endpoints, streaming SSE parse
│  ├─ brave.py              # Brave Search pass-through (/brave/res/v1/{path}) + per-second pacer
│  ├─ constants.py          # Package name, Anthropic API version, validation timeout
│  ├─ db.py                 # SQLAlchemy async engine + trace/cache_hits tables + DB URL redaction
│  ├─ errors.py             # The proxy's own error envelope, shared by every route
│  ├─ gemini.py             # Gemini SDK adapter (chat + embeddings) + OpenAI response mapping
│  ├─ libraries.md          # Library reference notes (httpx.AsyncClient)
│  ├─ models.py             # Concurrent per-provider model listing, models.dev enrichment + disk caching
│  ├─ openai.py             # OpenAI response/chunk builders (chat completion, stream chunk, done chunk, embedding response, usage mapping)
│  ├─ providers.py          # Provider parsing, default model, auth config, base URL + endpoint URL builders (chat + embeddings)
│  ├─ ratelimit.py          # Client-side rate limiter (sliding window, RPM + TPM, per-model limits, live limits, stale eviction)
│  ├─ rest.py               # Generic REST pass-through (/rest/{name}/{path}) with opt-in TTL cache
│  ├─ retry.py              # Retry config, Retry-After parsing, Gemini retry-hint parsing, backoff delay
│  ├─ schemas.py            # Pydantic request models (ChatCompletionRequest, EmbeddingRequest; model optional, extra=allow)
│  ├─ tracing.py            # Trace persistence (asyncio.shield), cache-hit logging, idempotency + response-cache + REST TTL-cache lookup
│  ├─ utility.py            # Logging helpers (validation result formatting)
│  └─ validation.py         # Concurrent provider key validation + in-memory caching
└─ web/                      # Next.js trace viewer frontend (App Router, TypeScript, Tailwind)
   ├─ package.json           # Frontend deps (next, react, tailwindcss, pg)
   ├─ next.config.ts         # Next.js config
   ├─ src/lib/db.ts          # Shared Postgres pool (pg.Pool, LLM_PROXY_DATABASE_URL)
   ├─ src/app/               # Pages: dashboard (/), traces (split-pane), latency (scatter per model), cache hits (split-pane)
   ├─ src/app/api/traces/    # API routes querying Postgres directly
   └─ src/components/        # Nav, JsonTree, BarChart, LatencyScatter, TraceTable, CacheHitDetailPanel, filters, badges
```
