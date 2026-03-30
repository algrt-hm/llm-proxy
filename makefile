.POSIX:

# Requires the below env var to be set e.g.
# LLM_PROXY_DATABASE_URL=postgresql://postgres@$$HOSTNAME/llm-proxy

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Available targets:"
	@echo "  help               Show available make targets"
	@echo "  run		            Start the FastAPI server on port 6969"
	@echo "  run-alt-postgres   Start on port 6969 using Postgres (LLM_PROXY_DATABASE_URL)"
	@echo "  ping-providers     Smoke-test providers through local proxy (uses credits)"
	@echo "  ping-providers-all Smoke-test all providers with cheap model defaults"
	@echo "  validate-keys      Validate upstream API keys directly (free/read-only checks)"
	@echo "  validate_keys      Alias for validate-keys"
	@echo "  format             Run ruff auto-fix and formatting"
	@echo "  lint               Run ruff checks without applying fixes"
	@echo "  test               Run unit tests"
	@echo "  stop               Kill running llm-proxy instance"
	@echo "  stats-sqlite       Show trace and cache hit statistics (SQLite)"
	@echo "  stats-postgres     Show trace/cache stats from Postgres (LLM_PROXY_DATABASE_URL)"
	@echo "  install-web        Install frontend dependencies (npm)"
	@echo "  dev-web            Start frontend dev server on port 3123"
	@echo "  build-web          Build frontend for production"
	@echo "  lint-web           Lint frontend code"

CHEAP_OPENAI_MODEL = gpt-4o-mini
CHEAP_OPENROUTER_MODEL = moonshotai/kimi-k2.5
CHEAP_PERPLEXITY_MODEL = sonar-pro
CHEAP_GEMINI_MODEL = gemini-3-flash-preview
CHEAP_CEREBRAS_MODEL = llama3.1-8b
CHEAP_ANTHROPIC_MODEL = claude-3-haiku-20240307

run:
	LLM_PROXY_PORT=6969 uv run python main.py

run-alt-postgres:
	@if [ -z "$$LLM_PROXY_DATABASE_URL" ]; then echo "LLM_PROXY_DATABASE_URL is not set"; exit 1; fi
	LLM_PROXY_RUN_ALT_POSTGRES=1 LLM_PROXY_PORT=6969 LLM_PROXY_DB_URL=$$(echo "$$LLM_PROXY_DATABASE_URL" | sed 's/postgresql:/postgresql+asyncpg:/') nice -n20 uv run python main.py

ping-providers:
	LLM_PROXY_BASE_URL=http://localhost:6969 \
	uv run python scripts/ping_providers.py

ping-providers-all:
	LLM_PROXY_BASE_URL=http://localhost:6969 \
	LLM_PROXY_TEST_FORCE_ALL=1 \
	LLM_PROXY_TEST_MODEL_OPENAI=$(CHEAP_OPENAI_MODEL) \
	LLM_PROXY_TEST_MODEL_OPENROUTER=$(CHEAP_OPENROUTER_MODEL) \
	LLM_PROXY_TEST_MODEL_PERPLEXITY=$(CHEAP_PERPLEXITY_MODEL) \
	LLM_PROXY_TEST_MODEL_GEMINI=$(CHEAP_GEMINI_MODEL) \
	LLM_PROXY_TEST_MODEL_CEREBRAS=$(CHEAP_CEREBRAS_MODEL) \
	LLM_PROXY_TEST_MODEL_ANTHROPIC=$(CHEAP_ANTHROPIC_MODEL) \
	uv run python scripts/ping_providers.py

stop:
	@sh scripts/stop.sh

validate-keys:
	uv run python scripts/validate_keys.py

validate_keys: validate-keys

format:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .

test:
	uv run pytest tests/

stats-sqlite:
	@echo "=== Traces by provider/model ==="
	@sqlite3 -header -column llmproxy.db \
		"SELECT provider, model, traces, avg_ms, ok, errors, tok_in, tok_out, tok_total, tok_think FROM ( \
		SELECT provider, model, COUNT(*) AS traces, \
		ROUND(AVG(latency_ms), 0) AS avg_ms, \
		SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS ok, \
		SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END) AS errors, \
		COALESCE(SUM(json_extract(response_json, '$$.usage.prompt_tokens')), 0) AS tok_in, \
		COALESCE(SUM(json_extract(response_json, '$$.usage.completion_tokens')), 0) AS tok_out, \
		COALESCE(SUM(json_extract(response_json, '$$.usage.total_tokens')), 0) AS tok_total, \
		COALESCE(SUM(json_extract(response_json, '$$.usage.total_tokens')), 0) \
		- COALESCE(SUM(json_extract(response_json, '$$.usage.prompt_tokens')), 0) \
		- COALESCE(SUM(json_extract(response_json, '$$.usage.completion_tokens')), 0) AS tok_think, \
		0 AS _sort \
		FROM traces GROUP BY provider, model \
		UNION ALL \
		SELECT '---', '---', COUNT(*), \
		ROUND(AVG(latency_ms), 0), \
		SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END), \
		SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END), \
		COALESCE(SUM(json_extract(response_json, '$$.usage.prompt_tokens')), 0), \
		COALESCE(SUM(json_extract(response_json, '$$.usage.completion_tokens')), 0), \
		COALESCE(SUM(json_extract(response_json, '$$.usage.total_tokens')), 0), \
		COALESCE(SUM(json_extract(response_json, '$$.usage.total_tokens')), 0) \
		- COALESCE(SUM(json_extract(response_json, '$$.usage.prompt_tokens')), 0) \
		- COALESCE(SUM(json_extract(response_json, '$$.usage.completion_tokens')), 0), \
		1 FROM traces) ORDER BY _sort, traces DESC;"
	@echo ""
	@echo "=== Cache hits ==="
	@sqlite3 -header -column llmproxy.db \
		"SELECT provider, model, cache_type, COUNT(*) AS hits \
		FROM cache_hits GROUP BY provider, model, cache_type ORDER BY hits DESC;" 2>/dev/null || echo "(no cache_hits table yet)"
	@echo ""
	@echo "=== Cache hit rate (by provider/model) ==="
	@sqlite3 -header -column llmproxy.db \
		"SELECT t.provider, t.model, \
		COUNT(DISTINCT t.id) AS traces, \
		COALESCE(c.cache_hits, 0) AS cache_hits, \
		ROUND(COALESCE(c.cache_hits, 0) * 100.0 / (COUNT(DISTINCT t.id) + COALESCE(c.cache_hits, 0)), 1) AS hit_pct \
		FROM traces t \
		LEFT JOIN (SELECT provider, model, COUNT(*) AS cache_hits FROM cache_hits GROUP BY provider, model) c \
		ON t.provider = c.provider AND t.model = c.model \
		WHERE t.status_code >= 200 AND t.status_code < 300 \
		GROUP BY t.provider, t.model ORDER BY traces DESC;" 2>/dev/null || echo "(no cache_hits table yet)"

stats-postgres:
	@if [ -z "$$LLM_PROXY_DATABASE_URL" ]; then echo "LLM_PROXY_DATABASE_URL is not set"; exit 1; fi
	@command -v psql >/dev/null 2>&1 || { echo "psql is required (install PostgreSQL client tools)"; exit 1; }
	@echo "=== Traces by provider/model ==="
	@psql "$$LLM_PROXY_DATABASE_URL" \
		-c "SELECT provider, model, traces, avg_ms, ok, errors, tok_in, tok_out, tok_total, tok_think FROM ( \
		SELECT provider, model, COUNT(*) AS traces, \
		ROUND(AVG(latency_ms)::numeric, 0) AS avg_ms, \
		SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END) AS ok, \
		SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END) AS errors, \
		COALESCE(SUM((response_json::jsonb #>> '{usage,prompt_tokens}')::bigint), 0) AS tok_in, \
		COALESCE(SUM((response_json::jsonb #>> '{usage,completion_tokens}')::bigint), 0) AS tok_out, \
		COALESCE(SUM((response_json::jsonb #>> '{usage,total_tokens}')::bigint), 0) AS tok_total, \
		COALESCE(SUM((response_json::jsonb #>> '{usage,total_tokens}')::bigint), 0) \
		- COALESCE(SUM((response_json::jsonb #>> '{usage,prompt_tokens}')::bigint), 0) \
		- COALESCE(SUM((response_json::jsonb #>> '{usage,completion_tokens}')::bigint), 0) AS tok_think, \
		0 AS _sort \
		FROM traces GROUP BY provider, model \
		UNION ALL \
		SELECT '---', '---', COUNT(*), \
		ROUND(AVG(latency_ms)::numeric, 0), \
		SUM(CASE WHEN status_code >= 200 AND status_code < 300 THEN 1 ELSE 0 END), \
		SUM(CASE WHEN status_code IS NULL OR status_code >= 400 THEN 1 ELSE 0 END), \
		COALESCE(SUM((response_json::jsonb #>> '{usage,prompt_tokens}')::bigint), 0), \
		COALESCE(SUM((response_json::jsonb #>> '{usage,completion_tokens}')::bigint), 0), \
		COALESCE(SUM((response_json::jsonb #>> '{usage,total_tokens}')::bigint), 0), \
		COALESCE(SUM((response_json::jsonb #>> '{usage,total_tokens}')::bigint), 0) \
		- COALESCE(SUM((response_json::jsonb #>> '{usage,prompt_tokens}')::bigint), 0) \
		- COALESCE(SUM((response_json::jsonb #>> '{usage,completion_tokens}')::bigint), 0), \
		1 FROM traces) AS summary ORDER BY _sort, traces DESC;"
	@echo ""
	@echo "=== Cache hits ==="
	@psql "$$LLM_PROXY_DATABASE_URL" \
		-c "SELECT provider, model, cache_type, COUNT(*) AS hits \
		FROM cache_hits GROUP BY provider, model, cache_type ORDER BY hits DESC;" 2>/dev/null || echo "(no cache_hits table yet)"
	@echo ""
	@echo "=== Cache hit rate (by provider/model) ==="
	@psql "$$LLM_PROXY_DATABASE_URL" \
		-c "SELECT t.provider, t.model, \
		COUNT(DISTINCT t.id) AS traces, \
		COALESCE(c.cache_hits, 0) AS cache_hits, \
		ROUND(COALESCE(c.cache_hits, 0) * 100.0 / (COUNT(DISTINCT t.id) + COALESCE(c.cache_hits, 0)), 1) AS hit_pct \
		FROM traces t \
		LEFT JOIN (SELECT provider, model, COUNT(*) AS cache_hits FROM cache_hits GROUP BY provider, model) c \
		ON t.provider = c.provider AND t.model = c.model \
		WHERE t.status_code >= 200 AND t.status_code < 300 \
		GROUP BY t.provider, t.model, c.cache_hits ORDER BY traces DESC;" 2>/dev/null || echo "(no cache_hits table yet)"

install-web:
	cd web && npm install

dev-web:
	@if [ -z "$$LLM_PROXY_DATABASE_URL" ]; then echo "LLM_PROXY_DATABASE_URL is not set"; exit 1; fi
	cd web && npm run dev -- -p 3123

build-web:
	cd web && npm run build

lint-web:
	cd web && npm run lint
