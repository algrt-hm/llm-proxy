import asyncio
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
    build_openai_done_chunk,
    build_openai_embedding_response,
    build_openai_response,
    build_openai_stream_chunk,
    generate_gemini_embedding,
    generate_gemini_response,
    stream_gemini_response,
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
from .ratelimit import GeminiRateLimiter, OpenAIRateLimiter
from .retry import (
    MAX_RETRIES,
    RETRYABLE_STATUSES,
    compute_delay,
    parse_gemini_retry_after,
)
from .schemas import ChatCompletionRequest, EmbeddingRequest
from .tracing import (
    lookup_idempotency,
    lookup_response_cache,
    record_cache_hit,
    record_trace,
)
from .validation import ProviderStatus, get_or_validate

TIMEOUT_S = float(getenv("LLM_PROXY_TIMEOUT_S", "300"))
MAX_IDEMPOTENCY_KEY_LEN = 256
LOGGER = logging.getLogger("llmproxy")

gemini_rate_limiter = GeminiRateLimiter()
openai_rate_limiter = OpenAIRateLimiter()


def _log_validation_results(results: dict[str, ProviderStatus]) -> None:
    valid = [n for n, s in results.items() if s.ok]
    invalid = [
        n
        for n, s in results.items()
        if not s.ok and s.detail != "no API key configured"
    ]
    unconfigured = [
        n for n, s in results.items() if s.detail == "no API key configured"
    ]

    if valid:
        LOGGER.info("Valid providers: %s", ", ".join(valid))
    if invalid:
        for name in invalid:
            LOGGER.warning("Invalid provider %s: %s", name, results[name].detail)
    if unconfigured:
        LOGGER.info("Unconfigured providers: %s", ", ".join(unconfigured))
    if not valid and not invalid:
        LOGGER.warning("No provider API keys configured.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"LLM_PROXY_DB_URL={redact_db_url(DB_URL)}")
    await test_db_connectivity()
    await init_db()
    provider_status = await get_or_validate()
    app.state.provider_status = provider_status
    _log_validation_results(provider_status)
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
    return {
        "providers": {
            name: {"ok": s.ok, "detail": s.detail} for name, s in results.items()
        }
    }


def _filter_by_created(models: list[dict], after: str | None) -> list[dict]:
    if not after:
        return models
    return [m for m in models if m.get("created", "") >= after]


@app.get("/v1/models/{provider}")
async def list_models(
    provider: str, request: Request, after: str | None = None
) -> JSONResponse:
    provider = provider.lower()
    if provider not in PROVIDERS:
        return JSONResponse(
            status_code=404,
            content={"error": {"message": f"Unknown provider: {provider}"}},
        )
    models = request.app.state.provider_models
    data = _filter_by_created(models.get(provider, []), after)
    return JSONResponse(content={"object": "list", "data": data})


@app.get("/v1/models")
async def list_all_models(request: Request, after: str | None = None) -> JSONResponse:
    models = request.app.state.provider_models
    data = []
    for provider, model_list in models.items():
        for m in _filter_by_created(model_list, after):
            entry = {**m, "id": f"{provider}/{m['id']}", "owned_by": provider}
            data.append(entry)
    return JSONResponse(content={"object": "list", "data": data})


def _filter_response_headers(
    headers: httpx.Headers, *, include_content_encoding: bool = True
) -> dict[str, str]:
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
    raise ValueError(
        "Invalid 'cache' value. Expected boolean true/false "
        "(also accepts 1/0 or true/false strings)."
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
    system_instruction, contents = build_gemini_contents(
        incoming_payload.get("messages", [])
    )
    if not contents:
        message = "Gemini requests must include at least one non-system message."
        await record_trace(
            request_id=request_id,
            provider="gemini",
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
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
                    usage_metadata = (
                        getattr(chunk, "usage_metadata", None) or usage_metadata
                    )
            except Exception as exc:  # noqa: BLE001
                await record_trace(
                    request_id=request_id,
                    provider="gemini",
                    model=upstream_model,
                    status_code=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    request_payload=trace_payload,
                    response_payload=None,
                    error=str(exc),
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
                latency_ms=(time.perf_counter() - started) * 1000,
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
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                retry_after = parse_gemini_retry_after(exc)
                if retry_after is not None:
                    delay = max(delay, retry_after)
                LOGGER.warning(
                    "Retry %d/%d for gemini/%s after %.1fs (error=%s)",
                    attempt + 1,
                    MAX_RETRIES,
                    upstream_model,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

    if last_exc is not None:
        await record_trace(
            request_id=request_id,
            provider="gemini",
            model=upstream_model,
            status_code=None,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload=trace_payload,
            response_payload=None,
            error=str(last_exc),
            idempotency_key=idempotency_key,
        )
        return _error_response(
            status.HTTP_502_BAD_GATEWAY,
            f"Gemini request failed: {last_exc}",
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
        latency_ms=(time.perf_counter() - started) * 1000,
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
    incoming_payload = payload.model_dump(exclude_none=True)
    provider, upstream_model = parse_model(payload.model)
    stream = bool(payload.stream)

    cache_value = incoming_payload.get("cache", True)
    try:
        cache_enabled = _is_cache_enabled(cache_value)
    except ValueError as exc:
        message = str(exc)
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
            latency_ms=(time.perf_counter() - started) * 1000,
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
        cached = await lookup_idempotency(
            idempotency_key, provider=provider, model=upstream_model
        )
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Only serve cache for non-streaming traces (proper JSON, not {"raw": ...}).
            # Skip if cached response is from a different endpoint (e.g. embedding).
            if (
                cached_payload is not None
                and "raw" not in cached_payload
                and cached_payload.get("object") != "list"
            ):
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
            latency_ms=(time.perf_counter() - started) * 1000,
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
            if (
                cached_payload is not None
                and "raw" not in cached_payload
                and cached_payload.get("object") != "list"
            ):
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
        message = f"{provider.upper()}_BASE_URL is not set"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)
    if not config.api_key:
        if provider == "gemini":
            message = "GEMINI_API_KEY or GOOGLE_API_KEY is not set"
        else:
            message = f"{provider.upper()}_API_KEY is not set"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

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

    headers = {"Content-Type": "application/json"}
    auth_headers, auth_params = build_auth(config)
    headers.update(auth_headers)

    url = build_chat_url(config.base_url)

    client: httpx.AsyncClient = request.app.state.http_client

    if stream:
        # Streaming: retry on retryable status before streaming begins.
        openai_slot: list | None = None
        if provider == "openai":
            openai_slot = await openai_rate_limiter.acquire(upstream_model)
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
                if attempt < MAX_RETRIES:
                    delay = compute_delay(attempt, None)
                    LOGGER.warning(
                        "Retry %d/%d for %s/%s after %.1fs (timeout)",
                        attempt + 1,
                        MAX_RETRIES,
                        provider,
                        upstream_model,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
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
            except httpx.HTTPError as exc:
                if attempt < MAX_RETRIES:
                    delay = compute_delay(attempt, None)
                    LOGGER.warning(
                        "Retry %d/%d for %s/%s after %.1fs (error=%s)",
                        attempt + 1,
                        MAX_RETRIES,
                        provider,
                        upstream_model,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    request_payload=outgoing_payload,
                    response_payload=None,
                    error=str(exc),
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
            chunks: list[bytes] = []
            try:
                async for chunk in response.aiter_raw():
                    chunks.append(chunk)
                    yield chunk
            finally:
                await response.aclose()
                if openai_slot is not None:
                    openai_rate_limiter.record(openai_slot, 0)
                raw = b"".join(chunks).decode(errors="replace")
                await record_trace(
                    request_id=request_id,
                    provider=provider,
                    model=upstream_model,
                    status_code=response.status_code,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    request_payload=outgoing_payload,
                    response_payload={"raw": raw},
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

    # Non-streaming HTTP: retry on retryable status or transient exceptions.
    response = None
    last_retry_after = None
    last_exc: Exception | None = None
    openai_slot = None
    for attempt in range(MAX_RETRIES + 1):
        if provider == "openai":
            openai_slot = await openai_rate_limiter.acquire(upstream_model)
        try:
            response = await client.post(
                url,
                headers=headers,
                params=auth_params,
                json=outgoing_payload,
            )
            last_exc = None
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (timeout)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (error=%s)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            break

        last_retry_after = response.headers.get("retry-after")
        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
            delay = compute_delay(attempt, last_retry_after)
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
        latency_ms = (time.perf_counter() - started) * 1000
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=None,
            latency_ms=latency_ms,
            request_payload=outgoing_payload,
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

    latency_ms = (time.perf_counter() - started) * 1000

    response_payload: Any
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        try:
            response_payload = response.json()
        except json.JSONDecodeError:
            response_payload = {"raw": response.text}
    else:
        response_payload = {"raw": response.text}

    if openai_slot is not None:
        prompt_tokens = 0
        if isinstance(response_payload, dict):
            usage = response_payload.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens", 0)
        openai_rate_limiter.record(openai_slot, prompt_tokens)

    await record_trace(
        request_id=request_id,
        provider=provider,
        model=upstream_model,
        status_code=response.status_code,
        latency_ms=latency_ms,
        request_payload=outgoing_payload,
        response_payload=response_payload,
        idempotency_key=idempotency_key,
    )

    resp_headers = _filter_response_headers(
        response.headers, include_content_encoding=False
    )
    resp_headers["x-request-id"] = request_id
    # Forward Retry-After on final failure with retryable status.
    if response.status_code in RETRYABLE_STATUSES and last_retry_after:
        resp_headers["retry-after"] = last_retry_after
    return JSONResponse(
        content=response_payload,
        status_code=response.status_code,
        headers=resp_headers,
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
    incoming_payload = payload.model_dump(exclude_none=True)
    provider, upstream_model = parse_model(payload.model)

    cache_value = incoming_payload.get("cache", True)
    try:
        cache_enabled = _is_cache_enabled(cache_value)
    except ValueError as exc:
        message = str(exc)
        error_trace_payload = {**incoming_payload, "model": upstream_model}
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
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
        cached = await lookup_idempotency(
            idempotency_key, provider=provider, model=upstream_model
        )
        if cached and cached.response_json:
            try:
                cached_payload = json.loads(cached.response_json)
            except json.JSONDecodeError:
                cached_payload = None
            # Skip if cached response is from a different endpoint (e.g. chat).
            if (
                cached_payload is not None
                and "raw" not in cached_payload
                and cached_payload.get("object") == "list"
            ):
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
            latency_ms=(time.perf_counter() - started) * 1000,
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
            if (
                cached_payload is not None
                and "raw" not in cached_payload
                and cached_payload.get("object") == "list"
            ):
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
        message = f"{provider.upper()}_BASE_URL is not set"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)
    if not config.api_key:
        if provider == "gemini":
            message = "GEMINI_API_KEY or GOOGLE_API_KEY is not set"
        else:
            message = f"{provider.upper()}_API_KEY is not set"
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=status.HTTP_400_BAD_REQUEST,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_payload=trace_payload,
            response_payload=None,
            error=message,
            idempotency_key=idempotency_key,
        )
        return _error_response(status.HTTP_400_BAD_REQUEST, message, request_id)

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
            latency_ms=(time.perf_counter() - started) * 1000,
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
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < MAX_RETRIES:
                    delay = compute_delay(attempt, None)
                    retry_after = parse_gemini_retry_after(exc)
                    if retry_after is not None:
                        delay = max(delay, retry_after)
                    LOGGER.warning(
                        "Retry %d/%d for gemini/%s after %.1fs (error=%s)",
                        attempt + 1,
                        MAX_RETRIES,
                        upstream_model,
                        delay,
                        exc,
                    )
                    await asyncio.sleep(delay)

        if last_exc is not None:
            await record_trace(
                request_id=request_id,
                provider="gemini",
                model=upstream_model,
                status_code=None,
                latency_ms=(time.perf_counter() - started) * 1000,
                request_payload=trace_payload,
                response_payload=None,
                error=str(last_exc),
                idempotency_key=idempotency_key,
            )
            return _error_response(
                status.HTTP_502_BAD_GATEWAY,
                f"Gemini embedding request failed: {last_exc}",
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
            latency_ms=(time.perf_counter() - started) * 1000,
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

    response = None
    last_retry_after = None
    last_exc = None
    openai_slot = None
    for attempt in range(MAX_RETRIES + 1):
        if provider == "openai":
            openai_slot = await openai_rate_limiter.acquire(upstream_model)
        try:
            response = await client.post(
                url,
                headers=http_headers,
                params=auth_params,
                json=outgoing_payload,
            )
            last_exc = None
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (timeout)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            break
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning(
                    "Retry %d/%d for %s/%s after %.1fs (error=%s)",
                    attempt + 1,
                    MAX_RETRIES,
                    provider,
                    upstream_model,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
                continue
            break

        last_retry_after = response.headers.get("retry-after")
        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
            delay = compute_delay(attempt, last_retry_after)
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
        latency_ms = (time.perf_counter() - started) * 1000
        await record_trace(
            request_id=request_id,
            provider=provider,
            model=upstream_model,
            status_code=None,
            latency_ms=latency_ms,
            request_payload=outgoing_payload,
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

    latency_ms = (time.perf_counter() - started) * 1000

    response_payload: Any
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        try:
            response_payload = response.json()
        except json.JSONDecodeError:
            response_payload = {"raw": response.text}
    else:
        response_payload = {"raw": response.text}

    if openai_slot is not None:
        prompt_tokens = 0
        if isinstance(response_payload, dict):
            usage = response_payload.get("usage")
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens", 0)
        openai_rate_limiter.record(openai_slot, prompt_tokens)

    await record_trace(
        request_id=request_id,
        provider=provider,
        model=upstream_model,
        status_code=response.status_code,
        latency_ms=latency_ms,
        request_payload=outgoing_payload,
        response_payload=response_payload,
        idempotency_key=idempotency_key,
    )

    resp_headers = _filter_response_headers(
        response.headers, include_content_encoding=False
    )
    resp_headers["x-request-id"] = request_id
    if response.status_code in RETRYABLE_STATUSES and last_retry_after:
        resp_headers["retry-after"] = last_retry_after
    return JSONResponse(
        content=response_payload,
        status_code=response.status_code,
        headers=resp_headers,
    )
