# AGENTS.md

## Reference

- `curl https://models.dev/api.json` for model names/info

## Repo overview
- Project: `llm-proxy` (FastAPI-based OpenAI-compatible proxy).
- Package name: `llmproxy` (note: not `llm_proxy`).
- Entry point: `main.py` runs `llmproxy.app:app` via uvicorn (optional `--require-db-url` exits early if `LLM_PROXY_DB_URL` is unset).
- Python: 3.14.1 (see `.python-version`).
- Tooling: `uv` for deps/run, `ruff` for formatting/linting, POSIX `make` targets.
- Do not use `from __future__ import annotations`; Python 3.14+ already has modern annotation behavior.
- Dependencies: includes `greenlet` (SQLAlchemy runtime requirement).

## API
- Health: `GET /health`.
- Status: `GET /status` — returns per-provider validation results (`ok`, `detail`).
- Models: `GET /v1/models` — returns all models across all configured providers (OpenAI-compatible format, prefixed `provider/model`).
- Models (per-provider): `GET /v1/models/{provider}` — returns models for a single provider.
- Chat: `POST /v1/chat/completions`.
- Embeddings: `POST /v1/embeddings` — OpenAI-compatible embedding endpoint. Supports `openai`, `openrouter`, and `gemini` providers. Request body: `model` + `input` (string or list of strings; empty input `[]` is rejected with 400). Extra fields like `encoding_format` and `dimensions` pass through to HTTP providers. Gemini uses the SDK adapter (`client.models.embed_content`). Includes tracing, response caching (embeddings are deterministic), idempotency, rate limiting, and retries.
- Chat/embeddings request caching: request body supports `cache` (default `true`); set `cache: false` to bypass response cache lookup for that request.
- Model routing: `provider:model` or `provider/model` prefix.
- Default provider: `LLM_PROXY_DEFAULT_PROVIDER` (default `openai`).

## File map
```
.
├─ .gitignore               # Git ignore rules (uv.lock, CLAUDE.md, .env, *.db, etc.)
├─ .python-version          # Python version pin
├─ AGENTS.md                # Agent notes (checked in)
├─ CLAUDE.md                # Agent notes (gitignored, local only)
├─ makefile                 # POSIX make targets (help/run/run-alt-postgres/stop/ping/validate/format/lint/test/stats-sqlite/stats-postgres/install-web/dev-web/build-web/lint-web)
├─ main.py                  # Uvicorn entrypoint for llmproxy.app:app
├─ pyproject.toml           # Dependencies + project metadata
├─ README.md                # Usage + env configuration
├─ uv.lock                  # Resolved dependency lockfile (gitignored)
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
│  └─ test_main_preflight.py # Preflight checks: `--require-db-url`, DB URL hostname validation, missing provider API keys
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
└─ web/                          # Next.js trace viewer frontend (App Router, TypeScript, Tailwind)
   ├─ package.json               # Frontend deps (next, react, pg; tailwindcss in devDeps)
   ├─ package-lock.json          # npm lockfile
   ├─ next.config.ts             # Next.js config
   ├─ tsconfig.json              # TypeScript config
   ├─ eslint.config.mjs          # ESLint flat config
   ├─ postcss.config.mjs         # PostCSS config (Tailwind)
   ├─ src/lib/
   │  ├─ db.ts                   # Shared Postgres pool (pg.Pool, DATABASE_URL)
   │  ├─ api.ts                  # Client-side fetch wrappers for API routes
   │  ├─ types.ts                # TypeScript interfaces (traces, stats, cache hits)
   │  ├─ utils.ts                # Formatting helpers (latency, tokens, dates)
   │  └─ constants.ts            # Time ranges, provider colors
   ├─ src/app/
   │  ├─ page.tsx                # Dashboard: summary cards, bar charts, stats table, token usage, cache hit rates
   │  ├─ traces/page.tsx         # Traces: split-pane list + detail, filters, hide-embeddings toggle
   │  ├─ traces/[id]/page.tsx    # Standalone trace detail (metadata + request/response JSON)
   │  ├─ cache-hits/page.tsx     # Cache hits: split-pane list + detail, filters
   │  └─ api/traces/             # Next.js API routes (query Postgres directly)
   │     ├─ route.ts             # GET /api/traces — paginated trace list
   │     ├─ [id]/route.ts        # GET /api/traces/:id — single trace with JSON bodies
   │     ├─ stats/route.ts       # GET /api/traces/stats — aggregate stats + tok_think
   │     ├─ providers/route.ts   # GET /api/traces/providers — distinct provider/model list
   │     ├─ cache-hits/route.ts  # GET /api/traces/cache-hits — paginated cache hits
   │     └─ cache-hits/stats/route.ts  # GET /api/traces/cache-hits/stats — cache hit rates
   └─ src/components/
      ├─ Nav.tsx                 # Sidebar navigation
      ├─ JsonTree.tsx            # Recursive collapsible JSON viewer (expandAll support)
      ├─ BarChart.tsx            # Pure SVG bar chart (formatValue support)
      ├─ StatsTable.tsx          # Provider/model stats table (tok_think column)
      ├─ TraceTable.tsx          # Trace list table (row selection)
      ├─ TraceDetailPanel.tsx    # Inline trace detail for split-pane view
      ├─ TraceFilters.tsx        # Provider/model/status/time filters + hide-embeddings
      ├─ CacheHitTable.tsx       # Cache hit list table (row selection)
      ├─ CacheHitDetailPanel.tsx # Inline cache hit detail for split-pane view
      ├─ Pagination.tsx          # Prev/Next pagination
      ├─ TimeRangeSelect.tsx     # 1h/6h/24h/7d/30d/All time range buttons
      ├─ ProviderBadge.tsx       # Colored provider label
      └─ StatusBadge.tsx         # Colored HTTP status badge
```

## Web frontend
- Next.js app in `web/` directory (App Router, TypeScript, Tailwind CSS v4).
- Port: `3123` (via `make dev-web`).
- Queries Postgres directly via Next.js API routes (`web/src/app/api/traces/`), using `pg` (node-postgres).
- Database connection: `LLM_PROXY_DATABASE_URL` env var (e.g. `postgresql://postgres@myhost/llm-proxy`); must be set before running `make dev-web`.
- Shared pool: `web/src/lib/db.ts`.
- Pages:
  - Dashboard (`/`): summary cards (traces, avg latency, success rate, cache hit rate), bar charts (traces/latency by provider in seconds), stats table with tok_think column, token usage summary (input/output/thinking/total), cache hit rates.
  - Traces (`/traces`): split-pane layout — left half shows filterable paginated trace list, clicking a row shows full trace detail (metadata + request/response JSON) in the right half. "Hide embeddings" checkbox (on by default) excludes embedding models. JSON trees are fully expanded by default.
  - Trace detail (`/traces/[id]`): standalone page with metadata, request/response JSON trees.
  - Cache hits (`/cache-hits`): split-pane layout — left half shows filterable paginated cache hit list, clicking a row shows cache hit metadata and the cached trace's request/response JSON in the right half.
- Stats match `make stats-sqlite` output: provider, model, traces, avg_ms, ok, errors, tok_in, tok_out, tok_total, tok_think, plus cache hit rates.
- Components: `JsonTree` (recursive collapsible JSON viewer with `expandAll` prop), `BarChart` (pure SVG with `formatValue` prop), `TraceDetailPanel` (inline trace detail for split-pane), `CacheHitDetailPanel` (inline cache hit detail for split-pane, fetches cached trace), filter/table/pagination components.
- Make targets: `install-web`, `dev-web`, `build-web`, `lint-web`.

## Providers
- Supported: `openai`, `openrouter`, `perplexity`, `gemini`, `cerebras`, `anthropic`.
- Default base URLs (override with `<PROVIDER>_BASE_URL`):
  - openai: `https://api.openai.com/v1`
  - openrouter: `https://openrouter.ai/api/v1`
  - perplexity: `https://api.perplexity.ai/v2`
  - gemini: `https://generativelanguage.googleapis.com/v1beta/openai` (defined but unused; SDK path is used instead)
  - cerebras: `https://api.cerebras.ai/v1`
  - anthropic: `https://api.anthropic.com/v1`
- API keys:
  - `<PROVIDER>_API_KEY` for all providers.
  - Gemini also accepts `GOOGLE_API_KEY`.
- Optional auth overrides (HTTP providers):
  - `<PROVIDER>_AUTH_HEADER` (default `Authorization`)
  - `<PROVIDER>_AUTH_PREFIX` (default `Bearer`)
  - `<PROVIDER>_AUTH_QUERY` (send key as query param instead of header; mutually exclusive with header auth)
- Startup logs which provider keys are configured vs missing.
- URL builders: `build_chat_url` appends `/chat/completions` to the base URL (no-op if already present). `build_embeddings_url` appends `/embeddings`, stripping `/chat/completions` suffix first if present (so a chat-style base URL produces a valid embeddings URL).

## Gemini SDK adapter
- Uses `google-genai` SDK for Gemini instead of OpenAI-compatible HTTP.
- Chat: maps OpenAI messages -> Gemini `contents` and `system_instruction`.
- Embeddings: calls `client.models.embed_content()` with all input texts in a single batched SDK call, maps results to OpenAI embedding response format.
- Streams are converted to OpenAI SSE chunks.
- Usage metadata is mapped to OpenAI usage fields.
- `GEMINI_BASE_URL` is ignored for SDK calls.
- Non-streaming failures include exception details in the 502 response.
- Unsupported multimodal content parts (e.g. `image_url`) are skipped with a warning; only text parts are forwarded to the SDK.
- OpenAI-style `reasoning` parameter is mapped to Gemini `ThinkingConfig`:
  - No `reasoning` → no `thinking_config` → model default (thinking models think normally).
  - `reasoning: {enabled: false}` → `ThinkingConfig(thinking_budget=0)` → thinking disabled.
  - `reasoning: {enabled: true, budget: N}` → `ThinkingConfig(thinking_budget=N)`.
  - `reasoning: {enabled: true}` (no budget) → no `thinking_config` → model default.
- For HTTP providers (OpenRouter, etc.), the `reasoning` field passes through to the upstream API unchanged (schema uses `extra="allow"`).

## Gemini rate limiting
- Client-side sliding-window rate limiter for Gemini API requests, per upstream model.
- Constants: `RPM = 22`, `TPM = 900_000`, `WINDOW = 60.0` (~10% below Gemini free-tier limits of 25 RPM / 1M TPM).
- Tracks both request count (RPM) and input tokens (TPM) over a 60-second window.
- Token tracking is retroactive: `acquire()` reserves a slot before the SDK call; `record()` fills in actual prompt tokens after the response. The request that pushes past the TPM limit still goes through, but subsequent requests wait until budget frees up.
- If either limit is reached, the request waits (sleeps) until the window slides enough to free capacity. Wait time is logged at INFO level.
- Non-streaming retries (chat and embeddings): each attempt acquires its own rate-limit slot, so retried requests are properly counted against RPM/TPM limits.
- Streaming: a single slot is acquired (no retries for Gemini streaming).
- On error (no `record()` call), the slot stays at 0 tokens — the request still counts toward RPM but adds no token cost.
- Stale per-model state (empty windows) is periodically evicted to bound memory usage.
- Only applies to Gemini; other providers use their own `RateLimiter` subclass (see OpenAI rate limiting).

## OpenAI rate limiting
- Client-side sliding-window rate limiter for OpenAI API requests, per upstream model.
- Uses the same `RateLimiter` base class as Gemini (60-second sliding window, RPM + TPM tracking).
- Default limits: `RPM = 5_000`, `TPM = 800_000`.
- Per-model overrides via `OPENAI_MODEL_LIMITS` in `ratelimit.py`:
  | Model prefix            | RPM   | TPM       |
  |-------------------------|-------|-----------|
  | `gpt-5.1`              | 5,000 | 2,000,000 |
  | `gpt-5-mini`           | 5,000 | 4,000,000 |
  | `gpt-5-nano`           | 5,000 | 4,000,000 |
  | `gpt-4.1`              | 5,000 | 800,000   |
  | `gpt-4.1-mini`         | 5,000 | 4,000,000 |
  | `gpt-4.1-nano`         | 5,000 | 4,000,000 |
  | `o3`                   | 5,000 | 800,000   |
  | `o4-mini`              | 5,000 | 4,000,000 |
  | `gpt-4o`               | 5,000 | 800,000   |
  | `gpt-4o-realtime-preview` | 5,000 | 800,000 |
  | `text-embedding-3-small` | 5,000 | 5,000,000 |
  | `text-embedding-3-large` | 5,000 | 5,000,000 |
- Model resolution uses prefix matching: `gpt-4o-2024-11-20` matches `gpt-4o`. Longest prefix wins (`gpt-4.1-mini-2025-04-14` matches `gpt-4.1-mini`, not `gpt-4.1`).
- Non-streaming: each retry attempt acquires its own rate-limit slot; `prompt_tokens` from the response `usage` object is recorded after JSON parse.
- Streaming: a single slot is acquired before the retry loop; tokens are recorded as 0 (TPM not extractable from SSE chunks).
- Only applies when `provider == "openai"`.

## Retries
- Transient upstream errors (429, 502, 503, 504) and timeout/connection exceptions are retried with exponential backoff.
- `LLM_PROXY_MAX_RETRIES` (default `2`) — max retry attempts (total attempts = retries + 1).
- `LLM_PROXY_RETRY_BASE_DELAY_S` (default `1.0`) — base delay; actual delay is `base * 2^attempt`, capped at 30 s.
- If upstream sends `Retry-After` header, delay is `max(retry_after, backoff)`.
- On final failure with `Retry-After`, the header is forwarded to the client.
- HTTP streaming: retried on initial response status before streaming begins; mid-stream errors are not retried.
- Gemini non-streaming: retried on SDK exceptions. Delay uses exponential backoff and, when present, a parsed Gemini SDK hint (for example `Please retry in 53s` / `retryDelay: '53s'`), taking the larger value.
- Gemini streaming: not retried (can't distinguish initial vs mid-stream errors).
- Retries logged at WARNING level: `"Retry %d/%d for %s/%s after %.1fs (status=%d)"`.

## Idempotency
- Clients may send an `Idempotency-Key` header with `POST /v1/chat/completions` or `POST /v1/embeddings`.
- Keys longer than 256 characters are rejected with a 400 response.
- Lookup is scoped by `(idempotency_key, provider, model)` — reusing a key across different providers or models will not return a cached response from the wrong context.
- Chat idempotency only applies to non-streaming requests; streaming requests skip the idempotency cache.
- If a previous successful (2xx) non-streaming trace exists for the same key/provider/model, the cached response is returned with `x-idempotency: cached` and the original `x-request-id`.
- Cross-endpoint safety: cached responses are validated by `object` field before serving. Chat expects `object != "list"`, embeddings expects `object == "list"`. A key reused across endpoints will not return the wrong response shape.
- Streaming traces (stored as `{"raw": ...}`) are not eligible for idempotency cache hits.
- The key is stored in the `idempotency_key` column of the `traces` table (nullable, indexed, max 256 chars).

## Response cache
- Non-streaming `POST /v1/chat/completions` and `POST /v1/embeddings` requests are cached in SQLite and looked up by `(provider, upstream model, normalized request payload)` via a SHA-256 cache key.
- Embeddings are always eligible for caching (never streaming). This is especially valuable since embeddings are deterministic.
- Cache lookup is on by default and runs even without `Idempotency-Key`.
- Per-request opt-out: set `cache: false` in the request body.
- Invalid `cache` values return `400` (expected boolean-like value).
- Cache hits return `x-cache: hit` and the original cached `x-request-id`.
- Cross-endpoint safety: cached responses are validated by `object` field before serving. Chat expects `object != "list"`, embeddings expects `object == "list"`. Hash collisions across endpoints will not return the wrong response shape.
- Degenerate chat completions (all choices have null or empty content) are not served from cache, preventing cache poisoning from flaky provider responses. The trace is still stored for debugging.
- Streaming traces (stored as `{"raw": ...}`) are not eligible for response cache hits.
- The computed hash is stored in the `cache_key` column of the `traces` table (nullable, indexed, 64-char hex).
- Both response cache and idempotency cache hits are logged to the `cache_hits` table (`provider`, `model`, `cache_type`, `cached_trace_id`, `request_json`).

## Database
- Supported backends: SQLite (3.35+) and Postgres.
- SQLAlchemy async ORM; migrations use `ADD COLUMN IF NOT EXISTS` (requires SQLite 3.35+).
- Default: `sqlite+aiosqlite:///./llmproxy.db` (override via `LLM_PROXY_DB_URL`).
- DB URL is logged at startup with passwords redacted (`redact_db_url`).

## Tracing
- Default DB file `llmproxy.db` (override `LLM_PROXY_DB_URL`).
- No truncation: full request/response JSON stored.
- Streaming (HTTP providers) buffers full raw response for trace.
- `x-request-id` header returned on success and errors.
- `idempotency_key` column stores the client-provided `Idempotency-Key` header value (nullable, indexed).
- `cache_key` column stores the computed request cache hash for response-cache lookup (nullable, indexed).

## Runtime env
- `LLM_PROXY_HOST` (default `0.0.0.0`)
- `LLM_PROXY_PORT` (default `8000`)
- `LLM_PROXY_RELOAD` (enable dev reload)
- `LLM_PROXY_TIMEOUT_S` (default `300`)
- `LLM_PROXY_MAX_RETRIES` (default `2`) — max retry attempts on transient upstream errors.
- `LLM_PROXY_RETRY_BASE_DELAY_S` (default `1.0`) — base backoff delay in seconds.
- `LLM_PROXY_VALIDATION_CACHE` (default `./llmproxy_validation.json`) — path to cached validation results; re-validates after 24 h.
- `LLM_PROXY_MODELS_CACHE` (default `./llmproxy_models.json`) — path to cached model lists; re-fetches after 24 h.
- No inbound auth; intended for internal use.
- Local run target: `make run` (port `6969`, SQLite DB).
- Alt Postgres run target: `make run-alt-postgres` (port `6969`, Postgres DB via `LLM_PROXY_DATABASE_URL`, runs at low CPU priority via `nice -n20`).
- Optional startup CLI arg: `--require-db-url` (exit with status 1 if `LLM_PROXY_DB_URL` is unset).

## Startup validation
- On startup, provider API keys are validated concurrently using free/read-only endpoints (same checks as `scripts/validate_keys.py`).
- Results are cached to `LLM_PROXY_VALIDATION_CACHE`; if the cache is fresh (< 24 h), validation is skipped.
- Results are exposed via `GET /status` and stored in `app.state.provider_status`.

## Model listing
- On startup, model lists are fetched concurrently from each configured provider (plus models.dev) and cached to `LLM_PROXY_MODELS_CACHE`.
- Cache is reused if fresh (< 24 h); otherwise models are re-fetched.
- Exposed via `GET /v1/models` (all providers) and `GET /v1/models/{provider}`.
- Per-provider fetch methods: openai/openrouter/cerebras use `GET /models`; anthropic uses paginated `GET /models`; gemini uses SDK `client.models.list()`; perplexity uses a hardcoded list (no listing API).
- Unconfigured providers (no API key) are skipped. Failed fetches are logged and stored as empty lists.
- After fetching, models are enriched with `context_length` and `max_output_tokens` from `https://models.dev/api.json`:
  - OpenRouter models already have `context_length` and are skipped.
  - Gemini models use their SDK-provided `input_token_limit` as `context_length`.
  - All other providers (openai, anthropic, cerebras, perplexity) get `context_length` and `max_output_tokens` from models.dev `limit.context` / `limit.output`.
  - If models.dev is unreachable, enrichment is skipped silently (warning logged).

## Process management
- Make target: `make stop` (kills running llm-proxy instance on `LLM_PROXY_PORT`, default 8000)
- Script: `scripts/stop.sh` (uses `lsof` to find and kill processes by port)

## Testing
- Make target: `make test` (runs `uv run pytest tests/`)
- Tests use `pytest` + `pytest-asyncio`.
- `tests/test_gemini.py` — unit tests for `build_gemini_config` (thinking/reasoning mapping).
- `tests/test_retry.py` — unit tests for retry-delay parsing, including Gemini SDK retry hints.
- `tests/test_payload_passthrough.py` — verifies `reasoning` and extra fields survive schema round-trip for HTTP providers; `build_embeddings_url` tests (plain, already-has-embeddings, chat-suffix stripping, trailing slash).
- `tests/test_ratelimit.py` — `GeminiRateLimiter` tests: window expiry, RPM/TPM blocking, concurrent requests, stale-model eviction, retry-per-attempt integration, and Gemini retry-hint delay handling.
- `tests/test_response_cache.py` — verifies default-on response cache hit, `cache: false` bypass, idempotency and response-cache cross-endpoint rejection (chat-on-embeddings and embedding-on-chat), empty embedding input rejection, and degenerate response rejection (null/empty content not served from cache).
- `tests/test_main_preflight.py` — preflight checks: DB URL hostname validation (accepts localhost, rejects missing/invalid), `GOOGLE_API_KEY` fallback for Gemini.

## Provider test scripts
- Script: `scripts/ping_providers.py`
- Make target: `make ping-providers`
- Make target (all providers, cheap defaults): `make ping-providers-all`
- Ping make targets set `LLM_PROXY_BASE_URL=http://localhost:6969`; local `localhost`/`127.0.0.1` values are normalized to port `6969` by the script.
- API keys in debug output are redacted (first 4 + last 4 characters shown).
- Script: `scripts/validate_keys.py` (free/read-only key validation)
- Make target: `make validate-keys`
- Make alias: `make validate_keys`
## Database & observability
- Trace DB URL (app/traces): `postgresql+asyncpg://postgres@$HOSTNAME/llm-proxy`
- Make target: `make stats-sqlite` (per-model trace counts, token usage, cache hit rates)
- Make target: `make stats-postgres` (same stats from `LLM_PROXY_DATABASE_URL`)
- `make stats-postgres` requires `LLM_PROXY_DATABASE_URL` to be set and `psql` installed/on `PATH`.

## Formatting and linting
- Make target: `make format` (runs `ruff check --fix` then `ruff format`)
- Make target: `make lint` (runs `ruff check`)
- `make` with no arguments shows a help/target list.
- Keep `makefile` POSIX-compatible (avoid GNU-specific make features).

### Ruff quirks (Python 3.14)
- Ruff removes parentheses from multi-exception `except` clauses: `except (ValueError, OSError):` becomes `except ValueError, OSError:`. This is correct — Python 3.14 (PEP 758) allows bare comma-separated exception types without parentheses in `except`. Do not "fix" this back to parenthesized form; ruff will just strip them again.

### Ping script required env vars
- `LLM_PROXY_TEST_MODEL_OPENAI`
- `LLM_PROXY_TEST_MODEL_OPENROUTER`
- `LLM_PROXY_TEST_MODEL_PERPLEXITY`
- `LLM_PROXY_TEST_MODEL_GEMINI`
- `LLM_PROXY_TEST_MODEL_CEREBRAS`
- `LLM_PROXY_TEST_MODEL_ANTHROPIC`

### Ping script optional env vars
- `LLM_PROXY_BASE_URL` (default: `http://localhost:6969`; local `localhost`/`127.0.0.1` values are normalized to port `6969`)

### Shared optional env vars
- `LLM_PROXY_TEST_TIMEOUT_S` (default: `60`)
- `LLM_PROXY_TEST_PROVIDERS` (comma-separated provider list)
- `LLM_PROXY_TEST_FORCE_ALL=1` (run even if local API key is missing)

### Validate script endpoint notes
- `openai`, `cerebras`: `GET /models`
- `openrouter`: `GET /auth/key`
- `perplexity`: `GET /async/chat/completions` (falls back to root host if configured base URL path returns 404)
- `anthropic`: `GET /models` with `anthropic-version` and `x-api-key` headers
- `gemini`: SDK `client.models.list()`

### Makefile defaults (override as needed)
- `CHEAP_OPENAI_MODEL` (default: `gpt-4o-mini`)
- `CHEAP_OPENROUTER_MODEL` (default: `moonshotai/kimi-k2.5`)
- `CHEAP_PERPLEXITY_MODEL` (default: `sonar-pro`)
- `CHEAP_GEMINI_MODEL` (default: `gemini-3-flash-preview`)
- `CHEAP_CEREBRAS_MODEL` (default: `llama3.1-8b`)
- `CHEAP_ANTHROPIC_MODEL` (default: `claude-3-haiku-20240307`)

### Notes
- The ping script only checks `<PROVIDER>_API_KEY` for presence; if you rely on `GOOGLE_API_KEY` for Gemini, set `LLM_PROXY_TEST_FORCE_ALL=1`.

### Example
```
LLM_PROXY_TEST_MODEL_OPENAI=gpt-4o-mini make ping-providers
```

### Reference

<https://models.dev/api.json> is a good source of model information covering the various providers
