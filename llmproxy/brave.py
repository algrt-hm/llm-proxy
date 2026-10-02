"""Brave Search API pass-through.

Brave is not an LLM provider, so it stays out of PROVIDERS (model parsing,
key validation and model listing would all misfire on it). Instead it gets its
own route that mirrors Brave's URL layout under ``/brave/res/v1``: a client
points its Brave base URL at ``http://<proxy>/brave/res/v1`` and keeps every
path and query parameter unchanged, while the proxy holds ``BRAVE_API_KEY`` and
records each call as a trace (provider ``brave``, model = the endpoint path).

Search results are never served from the response cache: the cache has no TTL,
and a web search is exactly the request whose answer should change over time.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from os import getenv

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import Response

from .constants import PACKAGE_NAME
from .errors import error_response
from .retry import MAX_RETRIES, RETRYABLE_STATUSES, compute_delay, parse_retry_after
from .tracing import record_trace

LOGGER = logging.getLogger(PACKAGE_NAME)

PROVIDER = "brave"
DEFAULT_BASE_URL = "https://api.search.brave.com/res/v1"
# Brave's endpoints are short lowercase paths (web/search, llm/context,
# summarizer/search, ...). Anything else, including "..", is refused rather
# than forwarded with the proxy's key attached.
PATH_RE = re.compile(r"^[a-z0-9_-]+(/[a-z0-9_-]+)*$")
# traces.model is VARCHAR(256); no real Brave endpoint is anywhere near this.
MAX_PATH_LEN = 128
# traces.model for refused paths, so client-chosen strings never reach that
# column (the raw path stays in the request payload).
INVALID_PATH_MODEL = "invalid-path"
# Request headers worth forwarding: content negotiation, API versioning, cache
# control and Brave's location hints. The client's own auth header is never
# forwarded; the proxy supplies the key.
FORWARDED_HEADERS = {"accept", "api-version", "cache-control", "content-type", "user-agent"}
FORWARDED_HEADER_PREFIX = "x-loc-"
# The shared client's timeout is sized for LLM generations (900s); a search
# that has not answered within seconds is not going to.
TIMEOUT_S = float(getenv("BRAVE_TIMEOUT_S", "30"))
# Brave meters per second (1 rps on the free plan). 0 disables pacing.
RPS = float(getenv("BRAVE_RPS", "1"))

router = APIRouter()


class Pacer:
    """Spaces upstream calls at least ``1/rps`` apart.

    Brave's quota is a per-second window, which the 60s sliding window in
    ``ratelimit.RateLimiter`` cannot express: a burst of five inside one second
    is within any per-minute budget and still draws four 429s.
    """

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / rps if rps > 0 else 0.0
        self._next_at = 0.0

    async def acquire(self) -> None:
        # No lock: the slot is read and reserved without an await in between.
        now = time.monotonic()
        wait = self._next_at - now
        self._next_at = max(now, self._next_at) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    def block(self, seconds: float) -> None:
        """Backpressure after an upstream 429: send nothing new for *seconds*."""
        self._next_at = max(self._next_at, time.monotonic() + seconds)


pacer = Pacer(RPS)


def brave_api_key() -> str | None:
    key = getenv("BRAVE_API_KEY")
    return key.strip() if key and key.strip() else None


def brave_base_url() -> str:
    return (getenv("BRAVE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _forwarded_headers(request: Request) -> dict[bytes, bytes]:
    # Bytes, not str: Starlette decodes header values as latin-1 and httpx
    # encodes str values as ASCII, so "X-Loc-City: São Paulo" would raise.
    # Lowercase keys so the client's Accept replaces the default.
    headers = {b"accept": b"application/json"}
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in FORWARDED_HEADERS or lowered.startswith(FORWARDED_HEADER_PREFIX):
            headers[lowered.encode("latin-1")] = value.encode("latin-1")
    return headers


def _retry_after(response: httpx.Response) -> str | None:
    """Retry-After, else the per-second window of X-RateLimit-Reset ("1, 1419704")."""
    reset = response.headers.get("x-ratelimit-reset", "").split(",")[0].strip()
    return response.headers.get("retry-after") or reset or None


@router.api_route("/brave/res/v1/{path:path}", methods=["GET", "POST"])
async def brave_proxy(path: str, request: Request) -> Response:
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    # Query params as a list of pairs: Brave accepts repeated keys (goggles).
    params = [(k, v) for k, v in request.query_params.multi_items() if k.lower() != "x-subscription-token"]
    trace_payload: dict[str, object] = {"path": path, "params": params}
    body: bytes | None = None
    if request.method == "POST":
        body = await request.body()
        trace_payload["method"] = "POST"
        try:
            trace_payload["body"] = json.loads(body)
        except ValueError:
            trace_payload["body"] = body.decode("utf-8", errors="replace")
    valid_path = len(path) <= MAX_PATH_LEN and PATH_RE.fullmatch(path) is not None

    async def trace(status_code: int | None, response_payload: object | None, error: str | None = None) -> None:
        await record_trace(
            request_id=request_id,
            provider=PROVIDER,
            model=path if valid_path else INVALID_PATH_MODEL,
            status_code=status_code,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=response_payload,
            error=error,
        )

    if not valid_path:
        await trace(status.HTTP_400_BAD_REQUEST, None, f"invalid Brave path {path[:MAX_PATH_LEN]!r}")
        return error_response(status.HTTP_400_BAD_REQUEST, f"Invalid Brave API path: {path[:MAX_PATH_LEN]!r}", request_id)

    api_key = brave_api_key()
    if api_key is None:
        await trace(status.HTTP_503_SERVICE_UNAVAILABLE, None, "BRAVE_API_KEY is not set")
        return error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "Brave Search is not configured (BRAVE_API_KEY is not set)", request_id)

    headers = _forwarded_headers(request)
    headers[b"x-subscription-token"] = api_key.encode("utf-8")
    url = f"{brave_base_url()}/{path}"
    client: httpx.AsyncClient = request.app.state.http_client

    response: httpx.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        # Every attempt takes its own slot, so retries are paced too.
        await pacer.acquire()
        try:
            response = await client.request(request.method, url, params=params, headers=headers, content=body, timeout=TIMEOUT_S)
        except httpx.TimeoutException as exception:
            await trace(None, None, str(exception))
            return error_response(status.HTTP_504_GATEWAY_TIMEOUT, "Upstream request timed out", request_id)
        except httpx.HTTPError as exception:
            if attempt < MAX_RETRIES:
                delay = compute_delay(attempt, None)
                LOGGER.warning("Retry %d/%d for brave/%s after %.1fs (error=%s)", attempt + 1, MAX_RETRIES, path, delay, exception)
                await asyncio.sleep(delay)
                continue
            await trace(None, None, str(exception))
            return error_response(status.HTTP_502_BAD_GATEWAY, "Upstream request failed", request_id)

        retry_after = _retry_after(response)
        if response.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            # Backpressure: hold queued searches until Brave's window resets.
            pacer.block(parse_retry_after(retry_after) or 1.0)
        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
            delay = compute_delay(attempt, retry_after)
            LOGGER.warning("Retry %d/%d for brave/%s after %.1fs (status=%d)", attempt + 1, MAX_RETRIES, path, delay, response.status_code)
            await asyncio.sleep(delay)
            continue
        break

    assert response is not None
    # Parsed only for the trace; the client gets Brave's bytes back untouched.
    try:
        trace_response: object = response.json()
    except ValueError:
        trace_response = {"raw": response.text}

    await trace(response.status_code, trace_response, None if response.is_success else f"Brave returned HTTP {response.status_code}")

    resp_headers = {"x-request-id": request_id}
    # Brave's quota headers are useful to the caller; pass them through.
    for name, value in response.headers.items():
        if name.lower().startswith("x-ratelimit-") or name.lower() == "retry-after":
            resp_headers[name] = value
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=resp_headers,
        media_type=response.headers.get("content-type"),
    )
