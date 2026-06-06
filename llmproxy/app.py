import asyncio
import codecs
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from os import getenv
from typing import Any

import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from .db import (
    DB_URL,
    init_db,
    redact_db_url,
    test_db_connectivity,
)
from .gemini import (
    build_gemini_config,
    build_gemini_contents,
    generate_gemini_embedding,
    generate_gemini_response,
    stream_gemini_response,
)
from .openai import (
    build_openai_done_chunk,
    build_openai_embedding_response,
    build_openai_response,
    build_openai_stream_chunk,
    usage_to_openai,
)
from .models import get_or_fetch_models
from .providers import (
    PROVIDERS,
    build_auth,
    build_chat_url,
    build_embeddings_url,
    get_provider_config,
    parse_model,
)
from .ratelimit import AnthropicRateLimiter, GeminiRateLimiter, OpenAIRateLimiter, estimate_tokens
from .retry import (
    MAX_RETRIES,
    RETRYABLE_STATUSES,
    compute_delay,
    parse_gemini_retry_after,
    parse_retry_after,
)
from .schemas import ChatCompletionRequest, EmbeddingRequest, OllamaShowRequest
from .tracing import (
    lookup_idempotency,
    lookup_response_cache,
    record_cache_hit,
    record_trace,
    trace_stats,
)
from .utility import log_validation_results
from .validation import ProviderStatus, get_or_validate
from .constants import PACKAGE_NAME

TIMEOUT_S = float(getenv("LLM_PROXY_TIMEOUT_S", "900"))
MAX_IDEMPOTENCY_KEY_LEN = 256
LOGGER = logging.getLogger(PACKAGE_NAME)

anthropic_rate_limiter = AnthropicRateLimiter()
gemini_rate_limiter = GeminiRateLimiter()
openai_rate_limiter = OpenAIRateLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"LLM_PROXY_DB_URL={redact_db_url(DB_URL)}")
    await test_db_connectivity()
    await init_db()
    provider_status = await get_or_validate()
    app.state.provider_status = provider_status
    log_validation_results(provider_status)
    provider_models = await get_or_fetch_models()
    app.state.provider_models = provider_models
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        app.state.http_client = client
        yield


app = FastAPI(title="llm-proxy", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
async def provider_status(request: Request) -> dict:
    results: dict[str, ProviderStatus] = request.app.state.provider_status
    response: dict = {"providers": {name: {"ok": s.ok, "detail": s.detail} for name, s in results.items()}}
    if trace_stats.trace_failures or trace_stats.cache_hit_failures:
        response["trace_errors"] = {
            "trace_failures": trace_stats.trace_failures,
            "cache_hit_failures": trace_stats.cache_hit_failures,
        }
    return response


@app.get("/version")
async def version() -> dict[str, str]:
    return {"version": PACKAGE_NAME}


@app.get("/props")
@app.get("/v1/props")
async def props() -> dict:
    return {
        "total_slots": 1,
        "chat_template": "",
        "default_generation_settings": {},
    }


@app.get("/api/tags")
async def ollama_tags(request: Request) -> dict:
    models = request.app.state.provider_models
    tags = []
    for provider, model_list in models.items():
        for m in model_list:
            tags.append(
                {
                    "name": f"{provider}/{m['id']}",
                    "model": f"{provider}/{m['id']}",
                    "modified_at": m.get("created", ""),
                }
            )
    return {"models": tags}


@app.post("/api/show")
async def ollama_show(request: Request, payload: OllamaShowRequest) -> JSONResponse:
    model_name = payload.name or payload.model or ""
    provider, upstream_model = parse_model(model_name)
    models = request.app.state.provider_models
    for m in models.get(provider, []):
        if m["id"] == upstream_model:
            return JSONResponse(
                content={
                    "modelfile": "",
                    "parameters": "",
                    "template": "",
                    "details": {"family": provider, "format": "gguf"},
                    "model_info": m,
                }
            )
    return JSONResponse(
        status_code=404,
        content={"error": f"model '{model_name}' not found"},
    )


def _filter_by_created(models: list[dict], after: str | None) -> list[dict]:
    if not after:
        return models
    return [m for m in models if m.get("created", "") >= after]


@app.get("/v1/models/{provider}/{model_id:path}")
async def get_model(provider: str, model_id: str, request: Request) -> JSONResponse:
    provider = provider.lower()
    if provider not in PROVIDERS:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"Unknown provider: {provider}"}},
        )
    models = request.app.state.provider_models
    for m in models.get(provider, []):
        if m["id"] == model_id:
            return JSONResponse(content={**m, "id": f"{provider}/{m['id']}", "owned_by": provider})
    return JSONResponse(
        status_code=404,
        content={"error": {"message": f"Model not found: {provider}/{model_id}"}},
    )


@app.get("/v1/models/{provider}")
async def list_models(provider: str, request: Request, after: str | None = None) -> JSONResponse:
    provider = provider.lower()
    if provider not in PROVIDERS:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"Unknown provider: {provider}"}},
        )
    models = request.app.state.provider_models
    data = _filter_by_created(models.get(provider, []), after)
    return JSONResponse(content={"object": "list", "data": data})


@app.get("/api/v1/models")
@app.get("/v1/models")
async def list_all_models(request: Request, after: str | None = None) -> JSONResponse:
    models = request.app.state.provider_models
    data = []
    for provider, model_list in models.items():
        for m in _filter_by_created(model_list, after):
            entry = {**m, "id": f"{provider}/{m['id']}", "owned_by": provider}
            data.append(entry)
    return JSONResponse(content={"object": "list", "data": data})


def _filter_response_headers(headers: httpx.Headers, *, include_content_encoding: bool = True) -> dict[str, str]:
    excluded = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
    }
    if not include_content_encoding:
        excluded.add("content-encoding")
    return {key: value for key, value in headers.items() if key.lower() not in excluded}


def _error_detail(message: str, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "llmproxy_error",
            "request_id": request_id,
        }
    }


def _error_response(
    status_code: int,
    message: str,
    request_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = {"x-request-id": request_id}
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        status_code=status_code,
        content=_error_detail(message, request_id),
        headers=headers,
    )


def _is_cache_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    raise ValueError("Invalid 'cache' value. Expected boolean true/false (also accepts 1/0 or true/false strings).")


def _inject_anthropic_cache_control(payload: dict) -> dict:
    """Add cache_control breakpoints to messages for Anthropic prompt caching.

    Marks up to two breakpoints:
    1. The system/developer message (stable across turns).
    2. The second-to-last message (caches conversation history).
    """
    messages = payload.get("messages")
    if not messages:
        return payload

    # Shallow-copy each message dict so we don't mutate trace_payload.
    messages = [dict(m) for m in messages]

    def _mark(msg: dict) -> None:
        content = msg.get("content")
        if content is None:
            return
        if isinstance(content, str):
            msg["content"] = [
                {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
            ]
        elif isinstance(content, list) and content:
            last = dict(content[-1])
            last["cache_control"] = {"type": "ephemeral"}
            msg["content"] = content[:-1] + [last]

    # Breakpoint 1: system/developer message
    for msg in messages:
        if msg.get("role") in ("system", "developer"):
            _mark(msg)
            break

    # Breakpoint 2: penultimate message (conversation history)
    if len(messages) >= 3:
        _mark(messages[-2])

    return {**payload, "messages": messages}


async def _proxy_http_request(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    payload: dict,
    provider: str,
    upstream_model: str,
    request_id: str,
    started: float,
    idempotency_key: str | None,
    est_tokens: int = 0,
) -> JSONResponse:
    """Non-streaming HTTP POST with retry, response parsing, and trace recording."""
    response = None
    last_retry_after = None
    last_exc: Exception | None = None
    openai_slot = None
    anthropic_slot = None
    for attempt in range(MAX_RETRIES + 1):
        if provider == "openai":
            openai_slot = await openai_rate_limiter.acquire(upstream_model, est_tokens)
        elif provider == "anthropic":
            anthropic_slot = await anthropic_rate_limiter.acquire(upstream_model, est_tokens)
        try:
            response = await client.post(url, headers=headers, params=params, json=payload)
            last_exc = None
        except httpx.TimeoutException as exception:
            last_exc = exception
            break
        except httpx.HTTPError as exception:
            last_exc = exception
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (error=%s)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                    exception,
                )
                await asyncio.sleep(delay)
                continue
            break

        last_retry_after = response.headers.get("retry-after")
        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
            delay = compute_delay(attempt, last_retry_after)
            # Backpressure: block the rate limiter so queued requests wait.
            if response.status_code == 429:
                block_secs = parse_retry_after(last_retry_after) or delay
                if provider == "openai":
                    openai_rate_limiter.block_model(upstream_model, block_secs)
                elif provider == "anthropic":
                    anthropic_rate_limiter.block_model(upstream_model, block_secs)
            LOGGER.warning(
                "Retry %d/%d for %s/%s after %.1fs (status=%d)",
                attempt + 1,
                MAX_RETRIES,
                provider,
                upstream_model,
                delay,
                response.status_code,
            )
            await asyncio.sleep(delay)
            continue
        break

    if last_exc is not None:
        latency_ms = round((time.perf_counter() - started) * 1000)
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=None,
            latency_ms=latency_ms,
            request_payload=payload,
            response_payload=None,
            error=str(last_exc),
            idempotency_key=idempotency_key,
        )
        if isinstance(last_exc, httpx.TimeoutException):
            return _error_response(
                status.HTTP_504_GATEWAY_TIMEOUT,
                "Upstream request timed out",
                request_id,
            )
        return _error_response(
            status.HTTP_502_BAD_GATEWAY,
            "Upstream request failed",
            request_id,
        )

    latency_ms = round((time.perf_counter() - started) * 1000)

    response_payload: Any
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        try:
            response_payload = response.json()
        except json.JSONDecodeError:
            response_payload = {"raw": response.text}
    else:
        response_payload = {"raw": response.text}

    if openai_slot is not None or anthropic_slot is not None:
        prompt_tokens = 0
        if isinstance(response_payload, dict):
            usage = response_payload.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens", 0)
        if openai_slot is not None:
            openai_rate_limiter.record(openai_slot, prompt_tokens)
        if anthropic_slot is not None:
            anthropic_rate_limiter.record(anthropic_slot, prompt_tokens)

    await record_trace(
        request_id=request_id,
        provider=provider,
        model=upstream_model,
        status_code=response.status_code,
        latency_ms=latency_ms,
        request_payload=payload,
        response_payload=response_payload,
        idempotency_key=idempotency_key,
    )

    resp_headers = _filter_response_headers(response.headers, include_content_encoding=False)
    resp_headers["x-request-id"] = request_id
    # Forward Retry-After on final failure with retryable status.
    if response.status_code in RETRYABLE_STATUSES and last_retry_after:
        resp_headers["retry-after"] = last_retry_after
    return JSONResponse(
        content=response_payload,
        status_code=response.status_code,
        headers=resp_headers,
    )


async def _handle_gemini_request(
    *,
    request_id: str,
    started: float,
    incoming_payload: dict[str, Any],
    trace_payload: dict[str, Any],
    upstream_model: str,
    api_key: str,
    stream: bool,
    idempotency_key: str | None = None,
) -> JSONResponse | StreamingResponse:
    system_instruction, contents = build_gemini_contents(incoming_payload.get("messages", []))
    if not contents:
        message = "Gemini requests must include at least one non-system message."
        await record_trace(
            request_id=request_id,
            provider="gemini",
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    gemini_config = build_gemini_config(incoming_payload, system_instruction)
    created = int(time.time())

    if stream:
        slot = await gemini_rate_limiter.acquire(upstream_model)

        # No retry for Gemini streaming — can't distinguish initial vs mid-stream errors.
        async def stream_body():
            chunks: list[str] = []
            usage_metadata: Any | None = None
            sent_role = False
            try:
                async for chunk in stream_gemini_response(
                    api_key=api_key,
                    model=upstream_model,
                    contents=contents,
                    config=gemini_config,
                ):
                    text = getattr(chunk, "text", None)
                    if text:
                        delta: dict[str, Any]
                        if not sent_role:
                            delta = {"role": "assistant", "content": text}
                            sent_role = True
                        else:
                            delta = {"content": text}
                        chunks.append(text)
                        yield build_openai_stream_chunk(
                            request_id=request_id,
                            model=upstream_model,
                            delta=delta,
                            finish_reason=None,
                            created=created,
                        )
                    usage_metadata = getattr(chunk, "usage_metadata", None) or usage_metadata
            except Exception as exception:  # noqa: BLE001
                gemini_rate_limiter.record(slot, 0)
                await record_trace(
                    request_id=request_id,
                    provider="gemini",
                    model=upstream_model,
                    status_code=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    request_payload=trace_payload,
                    response_payload=None,
                    error=str(exception),
                    idempotency_key=idempotency_key,
                )
                raise

            full_text = "".join(chunks)
            usage = usage_to_openai(usage_metadata)
            gemini_rate_limiter.record(slot, (usage or {}).get("prompt_tokens", 0))
            response_payload = build_openai_response(
                request_id=request_id,
                model=upstream_model,
                text=full_text,
                usage=usage,
                created=created,
            )
            await record_trace(
                request_id=request_id,
                provider="gemini",
                model=upstream_model,
                status_code=status.HTTP_200_OK,
                latency_ms=round((time.perf_counter() - started) * 1000),
                request_payload=trace_payload,
                response_payload=response_payload,
                idempotency_key=idempotency_key,
            )

            yield build_openai_stream_chunk(
                request_id=request_id,
                model=upstream_model,
                delta={},
                finish_reason="stop",
                created=created,
            )
            yield build_openai_done_chunk()

        return StreamingResponse(
            stream_body(),
            status_code=status.HTTP_200_OK,
            media_type="text/event-stream",
            headers={"x-request-id": request_id},
        )

    # Non-streaming Gemini: retry on exceptions.
    # Each attempt acquires its own rate-limit slot so retries are counted.
    last_exc: Exception | None = None
    slot: list | None = None
    for attempt in range(MAX_RETRIES + 1):
        slot = await gemini_rate_limiter.acquire(upstream_model)
        try:
            response = await generate_gemini_response(
                api_key=api_key,
                model=upstream_model,
                contents=contents,
                config=gemini_config,
            )
            last_exc = None
            break
        except Exception as exception:  # noqa: BLE001
            last_exc = exception
            gemini_rate_limiter.record(slot, 0)
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                retry_after = parse_gemini_retry_after(exception)
                if retry_after is not None:
                    delay = max(delay, retry_after)
                LOGGER.warning(
                    "Retry %d/%d for gemini/%s after %.1fs (error=%s)",
                    attempt + 1,
                    MAX_RETRIES,
                    upstream_model,
                    delay,
                    exception,
                )
                await asyncio.sleep(delay)

    if last_exc is not None:
        await record_trace(
            request_id=request_id,
            provider="gemini",
            model=upstream_model,
            status_code=None,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=str(last_exc),
            idempotency_key=idempotency_key,
        )
        return _error_response(
            status.HTTP_502_BAD_GATEWAY,
            "Gemini request failed",
            request_id,
        )

    text = getattr(response, "text", "")
    usage = usage_to_openai(getattr(response, "usage_metadata", None))
    gemini_rate_limiter.record(slot, (usage or {}).get("prompt_tokens", 0))
    response_payload = build_openai_response(
        request_id=request_id,
        model=upstream_model,
        text=text or "",
        usage=usage,
        created=created,
    )
    await record_trace(
        request_id=request_id,
        provider="gemini",
        model=upstream_model,
        status_code=status.HTTP_200_OK,
        latency_ms=round((time.perf_counter() - started) * 1000),
        request_payload=trace_payload,
        response_payload=response_payload,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        content=response_payload,
        status_code=status.HTTP_200_OK,
        headers={"x-request-id": request_id},
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, payload: ChatCompletionRequest):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    idempotency_key = request.headers.get("idempotency-key")
    if idempotency_key and len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LEN:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            f"Idempotency-Key exceeds maximum length of {MAX_IDEMPOTENCY_KEY_LEN}",
            request_id,
        )
    incoming_payload = payload.model_dump(exclude_unset=True)
    provider, upstream_model = parse_model(payload.model)
    stream = bool(payload.stream)

    cache_value = incoming_payload.get("cache", True)
    try:
        cache_enabled = _is_cache_enabled(cache_value)
    except ValueError as exception:
        message = str(exception)
        error_trace_payload = {
            **incoming_payload,
            "model": upstream_model,
            "stream": stream,
        }
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=error_trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    incoming_payload.pop("cache", None)
    trace_payload = {**incoming_payload, "model": upstream_model, "stream": stream}

    # --- Idempotency check (non-streaming only) ---
    if idempotency_key and not stream:
        cached = await lookup_idempotency(idempotency_key, provider=provider, model=upstream_model)
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Only serve cache for non-streaming traces (proper JSON, not {"raw": ...}).
            # Skip if cached response is from a different endpoint (e.g. embedding).
            if cached_payload is not None and "raw" not in cached_payload and cached_payload.get("object") != "list":
                await record_cache_hit(
                    provider=provider,
                    model=upstream_model,
                    cache_type="idempotency",
                    cached_trace_id=cached.id,
                    request_payload=trace_payload,
                )
                return JSONResponse(
                    content=cached_payload,
                    status_code=cached.status_code or 200,
                    headers={
                        "x-request-id": cached.request_id,
                        "x-idempotency": "cached",
                    },
                )

    if provider not in PROVIDERS:
        message = f"Unknown provider: {provider}"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    if cache_enabled and not stream:
        cached = await lookup_response_cache(
            provider=provider,
            model=upstream_model,
            request_payload=trace_payload,
        )
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Skip if cached response is from a different endpoint (e.g. embedding).
            if cached_payload is not None and "raw" not in cached_payload and cached_payload.get("object") != "list":
                await record_cache_hit(
                    provider=provider,
                    model=upstream_model,
                    cache_type="response",
                    cached_trace_id=cached.id,
                    request_payload=trace_payload,
                )
                return JSONResponse(
                    content=cached_payload,
                    status_code=cached.status_code or 200,
                    headers={
                        "x-request-id": cached.request_id,
                        "x-cache": "hit",
                    },
                )

    config = get_provider_config(provider)
    if provider != "gemini" and not config.base_url:
        detail = f"{provider.upper()}_BASE_URL is not set"
        message = f"Provider {provider!r} is not configured"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=detail,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, message, request_id)
    if not config.api_key:
        detail = "GEMINI_API_KEY or GOOGLE_API_KEY is not set" if provider == "gemini" else f"{provider.upper()}_API_KEY is not set"
        message = f"Provider {provider!r} is not configured"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=detail,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, message, request_id)

    if provider == "gemini":
        return await _handle_gemini_request(
            request_id=request_id,
            started=started,
            incoming_payload=incoming_payload,
            trace_payload=trace_payload,
            upstream_model=upstream_model,
            api_key=config.api_key,
            stream=stream,
            idempotency_key=idempotency_key,
        )

    outgoing_payload = trace_payload
    if provider == "anthropic":
        outgoing_payload = _inject_anthropic_cache_control(outgoing_payload)

    headers = {"Content-Type": "application/json"}
    auth_headers, auth_params = build_auth(config)
    headers.update(auth_headers)

    url = build_chat_url(config.base_url)

    client: httpx.AsyncClient = request.app.state.http_client

    if stream:
        # Ask upstream to include usage stats in the final streaming chunk.
        outgoing_payload = {
            **outgoing_payload,
            "stream_options": {"include_usage": True},
        }
        # Streaming: retry on retryable status before streaming begins.
        est_toks = estimate_tokens(outgoing_payload)
        openai_slot: list | None = None
        anthropic_slot: list | None = None
        if provider == "openai":
            openai_slot = await openai_rate_limiter.acquire(upstream_model, est_toks)
        elif provider == "anthropic":
            anthropic_slot = await anthropic_rate_limiter.acquire(upstream_model, est_toks)
        response: httpx.Response | None = None
        last_retry_after: str | None = None
        for attempt in range(MAX_RETRIES + 1):
            request_obj = client.build_request(
                "POST",
                url,
                headers=headers,
                params=auth_params,
                json=outgoing_payload,
            )
            try:
                response = await client.send(request_obj, stream=True)
            except httpx.TimeoutException:
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    request_payload=outgoing_payload,
                    response_payload=None,
                    error="Upstream request timed out",
                    idempotency_key=idempotency_key,
                )
                return _error_response(
                    status.HTTP_504_GATEWAY_TIMEOUT,
                    "Upstream request timed out",
                    request_id,
                )
            except httpx.HTTPError as exception:
                if attempt < MAX_RETRIES:
                    delay = compute_delay(attempt, None)
                    LOGGER.warning(
                        "Retry %d/%d for %s/%s after %.1fs (error=%s)",
                        attempt + 1,
                        MAX_RETRIES,
                        provider,
                        upstream_model,
                        delay,
                        exception,
                    )
                    await asyncio.sleep(delay)
                    continue
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=None,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    request_payload=outgoing_payload,
                    response_payload=None,
                    error=str(exception),
                    idempotency_key=idempotency_key,
                )
                return _error_response(
                    status.HTTP_502_BAD_GATEWAY,
                    "Upstream request failed",
                    request_id,
                )

            last_retry_after = response.headers.get("retry-after")
            if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                delay = compute_delay(attempt, last_retry_after)
                if response.status_code == 429:
                    block_secs = parse_retry_after(last_retry_after) or delay
                    if provider == "openai":
                        openai_rate_limiter.block_model(upstream_model, block_secs)
                    elif provider == "anthropic":
                        anthropic_rate_limiter.block_model(upstream_model, block_secs)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (status=%d)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                    response.status_code,
                )
                await response.aclose()
                await asyncio.sleep(delay)
                continue
            break

        async def stream_body():
            text_parts: list[str] = []
            usage: dict | None = None
            created: int | None = None
            buf = ""
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                async for raw_chunk in response.aiter_raw():
                    yield raw_chunk
                    buf += decoder.decode(raw_chunk)
                    # Process complete SSE lines from the buffer.
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if not line.startswith("data: ") or line == "data: [DONE]":
                            continue
                        try:
                            parsed = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if created is None:
                            created = parsed.get("created")
                        for choice in parsed.get("choices") or []:
                            content = (choice.get("delta") or {}).get("content")
                            if content:
                                text_parts.append(content)
                        if parsed.get("usage"):
                            usage = parsed["usage"]
                # Flush decoder and process any remaining data in the buffer.
                buf += decoder.decode(b"", final=True)
                remaining = buf.strip()
                if remaining.startswith("data: ") and remaining != "data: [DONE]":
                    try:
                        parsed = json.loads(remaining[6:])
                        if created is None:
                            created = parsed.get("created")
                        for choice in parsed.get("choices") or []:
                            content = (choice.get("delta") or {}).get("content")
                            if content:
                                text_parts.append(content)
                        if parsed.get("usage"):
                            usage = parsed["usage"]
                    except json.JSONDecodeError:
                        pass
            finally:
                try:
                    await response.aclose()
                except Exception:  # noqa: BLE001
                    LOGGER.warning("Failed to close upstream response for %s", request_id)
                reconstructed = build_openai_response(
                    request_id=request_id,
                    model=upstream_model,
                    text="".join(text_parts),
                    usage=usage,
                    created=created,
                )
                prompt_tokens = (usage or {}).get("prompt_tokens", 0)
                if openai_slot is not None:
                    openai_rate_limiter.record(openai_slot, prompt_tokens)
                if anthropic_slot is not None:
                    anthropic_rate_limiter.record(anthropic_slot, prompt_tokens)
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=response.status_code,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                    request_payload=outgoing_payload,
                    response_payload=reconstructed,
                    idempotency_key=idempotency_key,
                )

        filtered_headers = _filter_response_headers(response.headers)
        filtered_headers["x-request-id"] = request_id
        return StreamingResponse(
            stream_body(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
            headers=filtered_headers,
        )

    return await _proxy_http_request(
        client=client,
        url=url,
        headers=headers,
        params=auth_params,
        payload=outgoing_payload,
        provider=provider,
        upstream_model=upstream_model,
        request_id=request_id,
        started=started,
        idempotency_key=idempotency_key,
        est_tokens=estimate_tokens(outgoing_payload),
    )


@app.post("/v1/embeddings")
async def embeddings(request: Request, payload: EmbeddingRequest):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    idempotency_key = request.headers.get("idempotency-key")
    if idempotency_key and len(idempotency_key) > MAX_IDEMPOTENCY_KEY_LEN:
        return _error_response(
            status.HTTP_400_BAD_REQUEST,
            f"Idempotency-Key exceeds maximum length of {MAX_IDEMPOTENCY_KEY_LEN}",
            request_id,
        )
    incoming_payload = payload.model_dump(exclude_unset=True)
    provider, upstream_model = parse_model(payload.model)

    cache_value = incoming_payload.get("cache", True)
    try:
        cache_enabled = _is_cache_enabled(cache_value)
    except ValueError as exception:
        message = str(exception)
        error_trace_payload = {**incoming_payload, "model": upstream_model}
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=error_trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    incoming_payload.pop("cache", None)
    trace_payload = {**incoming_payload, "model": upstream_model}

    # --- Idempotency check ---
    if idempotency_key:
        cached = await lookup_idempotency(idempotency_key, provider=provider, model=upstream_model)
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Skip if cached response is from a different endpoint (e.g. chat).
            if cached_payload is not None and "raw" not in cached_payload and cached_payload.get("object") == "list":
                await record_cache_hit(
                    provider=provider,
                    model=upstream_model,
                    cache_type="idempotency",
                    cached_trace_id=cached.id,
                    request_payload=trace_payload,
                )
                return JSONResponse(
                    content=cached_payload,
                    status_code=cached.status_code or 200,
                    headers={
                        "x-request-id": cached.request_id,
                        "x-idempotency": "cached",
                    },
                )

    if provider not in PROVIDERS:
        message = f"Unknown provider: {provider}"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    # --- Response cache ---
    if cache_enabled:
        cached = await lookup_response_cache(
            provider=provider,
            model=upstream_model,
            request_payload=trace_payload,
        )
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Skip if cached response is from a different endpoint (e.g. chat).
            if cached_payload is not None and "raw" not in cached_payload and cached_payload.get("object") == "list":
                await record_cache_hit(
                    provider=provider,
                    model=upstream_model,
                    cache_type="response",
                    cached_trace_id=cached.id,
                    request_payload=trace_payload,
                )
                return JSONResponse(
                    content=cached_payload,
                    status_code=cached.status_code or 200,
                    headers={
                        "x-request-id": cached.request_id,
                        "x-cache": "hit",
                    },
                )

    config = get_provider_config(provider)
    if provider != "gemini" and not config.base_url:
        detail = f"{provider.upper()}_BASE_URL is not set"
        message = f"Provider {provider!r} is not configured"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=detail,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, message, request_id)
    if not config.api_key:
        detail = "GEMINI_API_KEY or GOOGLE_API_KEY is not set" if provider == "gemini" else f"{provider.upper()}_API_KEY is not set"
        message = f"Provider {provider!r} is not configured"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=detail,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, message, request_id)

    # Normalize input to list of strings.
    raw_input = payload.input
    input_texts = [raw_input] if isinstance(raw_input, str) else list(raw_input)

    if not input_texts:
        message = "input must not be empty"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

    # --- Gemini embedding via SDK ---
    if provider == "gemini":
        last_exc: Exception | None = None
        slot: list | None = None
        for attempt in range(MAX_RETRIES + 1):
            slot = await gemini_rate_limiter.acquire(upstream_model)
            try:
                emb_vectors = await generate_gemini_embedding(
                    api_key=config.api_key,
                    model=upstream_model,
                    texts=input_texts,
                )
                last_exc = None
                break
            except Exception as exception:  # noqa: BLE001
                last_exc = exception
                gemini_rate_limiter.record(slot, 0)
                if attempt < MAX_RETRIES:
                    delay = compute_delay(attempt, None)
                    retry_after = parse_gemini_retry_after(exception)
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                    LOGGER.warning(
                        "Retry %d/%d for gemini/%s after %.1fs (error=%s)",
                        attempt + 1,
                        MAX_RETRIES,
                        upstream_model,
                        delay,
                        exception,
                    )
                    await asyncio.sleep(delay)

        if last_exc is not None:
            await record_trace(
                request_id=request_id,
                provider="gemini",
                model=upstream_model,
                status_code=None,
                latency_ms=round((time.perf_counter() - started) * 1000),
                request_payload=trace_payload,
                response_payload=None,
                error=str(last_exc),
                idempotency_key=idempotency_key,
            )
            return _error_response(
                status.HTTP_502_BAD_GATEWAY,
                "Gemini embedding request failed",
                request_id,
            )

        response_payload = build_openai_embedding_response(
            model=upstream_model,
            embeddings=emb_vectors,
            input_texts=input_texts,
        )
        prompt_tokens = response_payload.get("usage", {}).get("prompt_tokens", 0)
        if slot is not None:
            gemini_rate_limiter.record(slot, prompt_tokens)
        await record_trace(
            request_id=request_id,
            provider="gemini",
            model=upstream_model,
            status_code=status.HTTP_200_OK,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=response_payload,
            idempotency_key=idempotency_key,
        )
        return JSONResponse(
            content=response_payload,
            status_code=status.HTTP_200_OK,
            headers={"x-request-id": request_id},
        )

    # --- HTTP provider embedding ---
    outgoing_payload = trace_payload
    http_headers = {"Content-Type": "application/json"}
    auth_headers, auth_params = build_auth(config)
    http_headers.update(auth_headers)
    url = build_embeddings_url(config.base_url)
    client: httpx.AsyncClient = request.app.state.http_client

    return await _proxy_http_request(
        client=client,
        url=url,
        headers=http_headers,
        params=auth_params,
        payload=outgoing_payload,
        provider=provider,
        upstream_model=upstream_model,
        request_id=request_id,
        started=started,
        idempotency_key=idempotency_key,
    )
