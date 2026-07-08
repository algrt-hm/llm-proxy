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

You select the model/provider via the `model` field
(Perplexity, OpenAI, OpenRouter, Google Gemini, Cerebras, Anthropic, DeepSeek).

Model selection supports a provider prefix, e.g. `openai:gpt-4o-mini`,
`openrouter:openai/gpt-4o-mini`, or `openai/gpt-4o-mini`.
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

## Runtime environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROXY_HOST` | `0.0.0.0` | Server bind address |
| `LLM_PROXY_PORT` | `8000` | Server port |
| `LLM_PROXY_RELOAD` | off | Enable dev auto-reload (`1`, `true`, or `yes`) |
| `LLM_PROXY_DEFAULT_PROVIDER` | `openai` | Provider used when model has no prefix |
| `LLM_PROXY_DEFAULT_MODEL` | `deepseek/deepseek-v4-pro` | Model (with optional provider prefix) used when `model` field is omitted or empty |
| `LLM_PROXY_TIMEOUT_S` | `900` | HTTP request timeout (seconds) |
| `LLM_PROXY_DB_URL` | `sqlite+aiosqlite:///./llmproxy.db` | SQLAlchemy database URL (SQLite 3.35+ and Postgres supported, e.g. `postgresql+asyncpg://user:pass@localhost/llmproxy`) |
| `LLM_PROXY_MAX_RETRIES` | `2` | Max retry attempts on transient upstream errors (429, 502, 503, 504) |
| `LLM_PROXY_RETRY_BASE_DELAY_S` | `1.0` | Base backoff delay (seconds); baseline delay is `base * 2^attempt` (capped at 30s inside shared retry helper) |
| `LLM_PROXY_MODELS_CACHE` | `./llmproxy_models.json` | Path to cached model lists (re-fetches after 24h) |
| `LLM_PROXY_DATABASE_URL` | — | Postgres connection string used by the web frontend and `make run`/`make stats-postgres` (plain `postgresql://`, e.g. `postgresql://postgres@myhost/llm-proxy`) |
| `LLM_PROXY_ANTHROPIC_RPM` | `50` | Anthropic client-side rate limiter: requests/minute (all models) |
| `LLM_PROXY_ANTHROPIC_TPM` | `30000` | Anthropic limiter: input tokens/minute for model families without a specific override |
| `LLM_PROXY_ANTHROPIC_OPUS_TPM` | `500000` | Anthropic limiter: input tokens/minute for `claude-opus*` models |
| `LLM_PROXY_ANTHROPIC_SONNET_TPM` | `30000` | Anthropic limiter: input tokens/minute for `claude-sonnet*` models |
| `LLM_PROXY_ANTHROPIC_HAIKU_TPM` | `50000` | Anthropic limiter: input tokens/minute for `claude-haiku*` models |

Full tracing is recorded to the database. Database interaction uses SQLAlchemy so the backend can be swapped (e.g. SQLite to Postgres).

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

## Formatting and linting

- `make format` (runs `uv run ruff check --fix .` then `uv run ruff format .`)

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
- Environment file: `~/.config/llm-proxy/env` (mode `600`; provider API keys plus `LLM_PROXY_DB_URL`, `LLM_PROXY_PORT`, `LLM_PROXY_RUN_ALT_POSTGRES`)
- Lingering is enabled (`loginctl enable-linger`), so the service starts at boot without a login

The unit file:

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

- `make ping-providers` (proxy chat smoke test; uses credits)
- `make ping-providers-all` (same, with cheap default models)
- Ping targets use `LLM_PROXY_BASE_URL=http://localhost:6969` and the ping script normalizes local base URLs to port `6969`.
- `make validate-keys` (direct upstream key validation; free/read-only endpoints)
- On server startup, llm-proxy runs the same free/read-only key validation logic as `make validate-keys` and caches results in-memory for the lifetime of the process.

## Web frontend

A Next.js trace viewer runs on port `3123` and queries Postgres directly (no FastAPI dependency).

The frontend reads `LLM_PROXY_DATABASE_URL` for its Postgres connection string (plain `postgresql://`, not the `+asyncpg` variant used by the Python backend).

```bash
export LLM_PROXY_DATABASE_URL="postgresql://postgres@myhost/llm-proxy"
make dev-web
```

- `make install-web` — install frontend dependencies
- `make dev-web` — start dev server on port `3123`
- `make build-web` — production build
- `make lint-web` — lint frontend code

Pages: Dashboard (`/`), Traces (`/traces` — split-pane with inline detail), Cache Hits (`/cache-hits` — split-pane with inline detail).

## Database & observability

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
│  ├─ test_gemini.py        # Gemini config builder tests (thinking/reasoning)
│  ├─ test_ratelimit.py     # Rate limiter tests (RPM/TPM, per-model limits, prefix matching, eviction, retry integration)
│  ├─ test_retry.py         # Retry delay parsing tests (Retry-After + Gemini SDK retry hints)
│  ├─ test_response_cache.py # Response cache, idempotency cross-endpoint rejection, empty input validation, degenerate response rejection tests
│  ├─ test_payload_passthrough.py  # Schema extra-field passthrough + URL builder tests
│  └─ test_main_preflight.py # Preflight checks: --require-db-url, DB URL validation, missing API keys
├─ llmproxy/
│  ├─ __init__.py           # Package version
│  ├─ app.py                # FastAPI app, routing, proxy logic (chat + embeddings), compatibility endpoints, streaming SSE parse
│  ├─ constants.py          # Package name, Anthropic API version, validation timeout
│  ├─ db.py                 # SQLAlchemy async engine + trace/cache_hits tables + DB URL redaction
│  ├─ gemini.py             # Gemini SDK adapter (chat + embeddings) + OpenAI response mapping
│  ├─ libraries.md          # Library reference notes (httpx.AsyncClient)
│  ├─ models.py             # Concurrent per-provider model listing, models.dev enrichment + disk caching
│  ├─ openai.py             # OpenAI response/chunk builders (chat completion, stream chunk, done chunk, embedding response, usage mapping)
│  ├─ providers.py          # Provider parsing, default model, auth config, base URL + endpoint URL builders (chat + embeddings)
│  ├─ ratelimit.py          # Client-side rate limiter (sliding window, RPM + TPM, per-model limits, stale eviction)
│  ├─ retry.py              # Retry config, Retry-After parsing, Gemini retry-hint parsing, backoff delay
│  ├─ schemas.py            # Pydantic request models (ChatCompletionRequest, EmbeddingRequest; model optional, extra=allow)
│  ├─ tracing.py            # Trace persistence (asyncio.shield), cache-hit logging, idempotency + response-cache lookup
│  ├─ utility.py            # Logging helpers (validation result formatting)
│  └─ validation.py         # Concurrent provider key validation + in-memory caching
└─ web/                      # Next.js trace viewer frontend (App Router, TypeScript, Tailwind)
   ├─ package.json           # Frontend deps (next, react, tailwindcss, pg)
   ├─ next.config.ts         # Next.js config
   ├─ src/lib/db.ts          # Shared Postgres pool (pg.Pool, DATABASE_URL)
   ├─ src/app/               # Pages: dashboard (/), traces (split-pane), cache hits (split-pane)
   ├─ src/app/api/traces/    # API routes querying Postgres directly
   └─ src/components/        # Nav, JsonTree, BarChart, TraceTable, CacheHitDetailPanel, filters, badges
```
