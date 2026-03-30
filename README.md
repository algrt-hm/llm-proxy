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

and enables the corresponding provider functionality.

llm-proxy implements the OpenAI chat and embeddings APIs

(see: https://app.stainless.com/api/spec/documented/openai/openapi.documented.yml)

and uses OpenAI-compatible endpoints. You select the model/provider via the `model` field
(Perplexity, OpenAI, OpenRouter, Google Gemini, Cerebras, Anthropic).

Model selection supports a provider prefix, e.g. `openai:gpt-4o-mini`,
`openrouter:openai/gpt-4o-mini`, or `openai/gpt-4o-mini`.
If no prefix is provided, `LLM_PROXY_DEFAULT_PROVIDER` is used (defaults to `openai`).

Each provider needs an API key. Base URLs have built-in defaults and can be overridden via:
- OPENAI_API_KEY / OPENAI_BASE_URL
- OPENROUTER_API_KEY / OPENROUTER_BASE_URL
- PERPLEXITY_API_KEY / PERPLEXITY_BASE_URL
- GEMINI_API_KEY (or GOOGLE_API_KEY)
- CEREBRAS_API_KEY / CEREBRAS_BASE_URL
- ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL

Gemini requests are sent via the official `google-genai` Python SDK rather than OpenAI-compatible HTTP calls.
`GEMINI_BASE_URL` is not used by the SDK path.

Optional auth overrides are available per provider:
- <PROVIDER>_AUTH_HEADER (default: Authorization)
- <PROVIDER>_AUTH_PREFIX (default: Bearer)
- <PROVIDER>_AUTH_QUERY (send API key as a query param instead)

Inbound authentication is not implemented; this is intended for internal use.

## Runtime environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROXY_HOST` | `0.0.0.0` | Server bind address |
| `LLM_PROXY_PORT` | `8000` | Server port |
| `LLM_PROXY_RELOAD` | off | Enable dev auto-reload (`1`, `true`, or `yes`) |
| `LLM_PROXY_DEFAULT_PROVIDER` | `openai` | Provider used when model has no prefix |
| `LLM_PROXY_TIMEOUT_S` | `300` | HTTP request timeout (seconds) |
| `LLM_PROXY_DB_URL` | `sqlite+aiosqlite:///./llmproxy.db` | SQLAlchemy database URL (SQLite 3.35+ and Postgres supported, e.g. `postgresql+asyncpg://user:pass@localhost/llmproxy`) |
| `LLM_PROXY_MAX_RETRIES` | `2` | Max retry attempts on transient upstream errors (429, 502, 503, 504) |
| `LLM_PROXY_RETRY_BASE_DELAY_S` | `1.0` | Base backoff delay (seconds); baseline delay is `base * 2^attempt` (capped at 30s inside shared retry helper) |
| `LLM_PROXY_VALIDATION_CACHE` | `./llmproxy_validation.json` | Path to cached provider validation results (re-validates after 24h) |
| `LLM_PROXY_MODELS_CACHE` | `./llmproxy_models.json` | Path to cached model lists (re-fetches after 24h) |
| `LLM_PROXY_DATABASE_URL` | — | Postgres connection string used by the web frontend and `make run-alt-postgres`/`make stats-postgres` (plain `postgresql://`, e.g. `postgresql://postgres@myhost/llm-proxy`) |

Full tracing is recorded to the database. Database interaction uses SQLAlchemy so the backend can be swapped (e.g. SQLite to Postgres).

## Retries

- Retryable upstream conditions: HTTP status `429/502/503/504`, timeout exceptions, and connection errors.
- Default retry count: `LLM_PROXY_MAX_RETRIES=2` (up to 3 total attempts).
- Baseline delay is exponential backoff: `LLM_PROXY_RETRY_BASE_DELAY_S * 2^attempt`.
- HTTP providers: `Retry-After` header is parsed and delay uses `max(retry_after, backoff)` through the shared helper.
- Gemini SDK (non-streaming chat + embeddings): retries are exception-driven and now parse retry hints from SDK error text (for example `Please retry in 53s` or `retryDelay: '53s'`). When present, the proxy waits at least that hinted duration before retrying.
- Gemini streaming requests are not retried.

## Response caching

- Non-streaming `POST /v1/chat/completions` and `POST /v1/embeddings` requests are cached by `(provider, model, request payload)` using the SQLite `traces` table.
- Embeddings are always eligible for caching (never streaming) and benefit especially since they are deterministic.
- Cache lookups are enabled by default.
- Disable cache lookup for a specific request with `cache: false` in the request body.
- Invalid `cache` values return `400` (expected boolean-like value).
- Streaming responses are not served from this cache.
- Degenerate chat completions (all choices have null or empty content) are not served from cache, preventing cache poisoning from flaky provider responses.
- Cache hits (both response and idempotency) are logged to a `cache_hits` table for observability.

## Formatting and linting

- `make format` (runs `uv run ruff check --fix .` then `uv run ruff format .`)

## Running locally

- `make run` (port `6969`, SQLite DB)
- `make run-alt-postgres` (port `6969`, Postgres DB via `LLM_PROXY_DATABASE_URL`, runs at low CPU priority via `nice -n20`)
- `uv run python main.py --require-db-url` (fail fast if `LLM_PROXY_DB_URL` is unset)

## Provider checks

- `make ping-providers` (proxy chat smoke test; uses credits)
- `make ping-providers-all` (same, with cheap default models)
- Ping targets use `LLM_PROXY_BASE_URL=http://localhost:6969` and the ping script normalizes local base URLs to port `6969`.
- `make validate-keys` (direct upstream key validation; free/read-only endpoints)
- On server startup, llm-proxy runs the same free/read-only key validation logic as `make validate-keys` and serves cached results from `LLM_PROXY_VALIDATION_CACHE` when fresh (< 24h).

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
├─ AGENTS.md                # Agent notes (checked in)
├─ CLAUDE.md                # Agent notes (gitignored, local only)
├─ makefile                 # Developer tasks (help/run/run-alt-postgres/stop/ping/validate/format/lint/test/stats-sqlite/stats-postgres/install-web/dev-web/build-web/lint-web)
├─ main.py                  # Uvicorn entrypoint for llmproxy.app:app
├─ pyproject.toml           # Dependencies + project metadata
├─ README.md                # Usage + env configuration
├─ uv.lock                  # Resolved dependency lockfile (gitignored, regenerated by uv)
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
│  ├─ app.py                # FastAPI app, routing, proxy logic (chat + embeddings), tracing hooks
│  ├─ providers.py          # Provider parsing, auth config, base URL + endpoint URL builders (chat + embeddings)
│  ├─ gemini.py             # Gemini SDK adapter (chat + embeddings) + OpenAI response mapping
│  ├─ schemas.py            # Pydantic request models (ChatCompletionRequest, EmbeddingRequest; extra=allow)
│  ├─ db.py                 # SQLAlchemy async engine + trace/cache_hits tables + DB URL redaction
│  ├─ retry.py              # Retry config, Retry-After parsing, Gemini retry-hint parsing, backoff delay
│  ├─ ratelimit.py          # Client-side rate limiter (sliding window, RPM + TPM, per-model limits, stale eviction)
│  ├─ tracing.py            # Trace persistence, cache-hit logging, provider/model-scoped idempotency + response-cache lookup
│  ├─ validation.py         # Concurrent provider key validation + disk caching
│  └─ models.py             # Concurrent per-provider model listing, models.dev enrichment + disk caching
└─ web/                      # Next.js trace viewer frontend (App Router, TypeScript, Tailwind)
   ├─ package.json           # Frontend deps (next, react, pg; tailwindcss in devDeps)
   ├─ package-lock.json      # npm lockfile
   ├─ next.config.ts         # Next.js config
   ├─ src/lib/db.ts          # Shared Postgres pool (pg.Pool, DATABASE_URL)
   ├─ src/app/               # Pages: dashboard (/), traces (split-pane), cache hits (split-pane)
   ├─ src/app/api/traces/    # API routes querying Postgres directly
   └─ src/components/        # Nav, JsonTree, BarChart, TraceTable, CacheHitDetailPanel, filters, badges
```
