"""Generic REST API pass-through.

Any HTTP API can be put behind the proxy by naming it in
``LLM_PROXY_REST_UPSTREAMS`` and giving it a base URL: a client then calls
``/rest/<name>/<path>`` instead of ``<base url>/<path>`` and keeps every path,
query parameter and body unchanged, while the proxy holds the credentials,
paces and retries the call, and records it as a trace (provider = the upstream
name, model = the path).

Unlike the LLM response cache, which never expires, caching here is opt-in per
upstream and bounded by a TTL (``<NAME>_CACHE_TTL_S``): most REST answers go
stale. Only ``GET`` is cached.

Per-upstream settings follow the LLM providers' ``<NAME>_*`` convention; see
``get_upstream``.
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from os import getenv
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Request, status
from fastapi.responses import Response

from .brave import PROVIDER as BRAVE_PROVIDER
from .brave import Pacer
from .constants import PACKAGE_NAME
from .errors import error_response
from .providers import PROVIDERS, ProviderConfig, build_auth
from .retry import MAX_RETRIES, RETRYABLE_STATUSES, compute_delay, parse_retry_after
from .tracing import lookup_rest_cache, record_cache_hit, record_trace

LOGGER = logging.getLogger(PACKAGE_NAME)

UPSTREAMS_ENV = "LLM_PROXY_REST_UPSTREAMS"
# The name is the traces.provider value (VARCHAR(32)) and, upper-cased, the
# prefix of the upstream's env vars.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# Names whose env vars or trace rows would collide with an existing route.
RESERVED_NAMES = {*PROVIDERS, BRAVE_PROVIDER}
# traces.model is VARCHAR(256); longer paths are cut for that column only (the
# full path stays in the request payload, and so in the cache key).
MAX_MODEL_LEN = 256
INVALID_PATH_MODEL = "invalid-path"
ROOT_PATH_MODEL = "/"
# Characters left unescaped when the decoded path is put back into a URL.
PATH_SAFE = "/:@!$&'()*+,;=-._~"
# Request headers forwarded by default; <NAME>_FORWARD_HEADERS adds more.
FORWARDED_HEADERS = {"accept", "accept-language", "content-type", "user-agent"}
# Never forwarded, whatever <NAME>_FORWARD_HEADERS says: the proxy supplies the
# credentials, and forwarded headers are stored in the trace.
CREDENTIAL_HEADERS = {"authorization", "cookie", "proxy-authorization"}
# Forwarded but kept out of the trace payload, so it does not split the cache.
UNTRACED_HEADERS = {"user-agent"}
# Upstream response headers not passed back: hop-by-hop headers, those httpx or
# Starlette recompute (the body arrives decoded), cookies, and a CDN's own
# X-Cache, which would read as this proxy's.
DROPPED_RESPONSE_HEADERS = {
    "x-cache",
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "keep-alive",
    "proxy-authenticate",
    "server",
    "set-cookie",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
# Repeating one of these cannot do the work twice, so any transient failure is
# retried. POST and PATCH are retried only after a 429, where the upstream
# refused the request rather than possibly having acted on it.
IDEMPOTENT_METHODS = {"GET", "PUT", "DELETE"}
DEFAULT_TIMEOUT_S = 30.0
# Non-JSON text responses are stored as exactly this pair of keys.
RAW_KEYS = {"raw", "content_type"}

router = APIRouter()


@dataclass(frozen=True)
class Upstream:
    config: ProviderConfig
    timeout_s: float
    rps: float
    cache_ttl_s: float
    forwarded_headers: frozenset[str]


_pacers: dict[str, Pacer] = {}


def upstream_names() -> list[str]:
    raw = getenv(UPSTREAMS_ENV, "")
    return [name for name in (part.strip().lower() for part in raw.split(",")) if name]


def _float_env(name: str, default: float) -> float:
    value = getenv(name, "").strip()
    return float(value) if value else default


def get_upstream(name: str) -> Upstream:
    """Read one upstream's settings from the environment.

    ``<NAME>_BASE_URL`` is required. Optional: ``<NAME>_API_KEY`` with the
    providers' ``<NAME>_AUTH_HEADER`` / ``_AUTH_PREFIX`` / ``_AUTH_QUERY``,
    ``<NAME>_TIMEOUT_S`` (30), ``<NAME>_RPS`` (0 = unpaced),
    ``<NAME>_CACHE_TTL_S`` (0 = not cached) and ``<NAME>_FORWARD_HEADERS``
    (comma-separated extra request headers to forward).

    Raises ValueError for a name that is not listed or is misconfigured.
    """
    if name not in upstream_names():
        raise ValueError(f"not listed in {UPSTREAMS_ENV}")
    if not NAME_RE.fullmatch(name):
        raise ValueError("name must match [a-z][a-z0-9_]{0,31}")
    if name in RESERVED_NAMES:
        raise ValueError("name is reserved for a built-in provider")
    key = name.upper()
    base_url = getenv(f"{key}_BASE_URL", "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"{key}_BASE_URL must be set to an http(s) URL")
    api_key = getenv(f"{key}_API_KEY", "").strip() or None
    extra_headers = {header.strip().lower() for header in getenv(f"{key}_FORWARD_HEADERS", "").split(",") if header.strip()}
    config = ProviderConfig(
        name=name,
        api_key=api_key,
        base_url=base_url,
        auth_header=getenv(f"{key}_AUTH_HEADER", "Authorization"),
        auth_prefix=getenv(f"{key}_AUTH_PREFIX", "Bearer"),
        auth_query_param=getenv(f"{key}_AUTH_QUERY") or None,
    )
    blocked = set(CREDENTIAL_HEADERS)
    if api_key and config.auth_header and not config.auth_query_param:
        blocked.add(config.auth_header.lower())
    return Upstream(
        config=config,
        timeout_s=_float_env(f"{key}_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        rps=_float_env(f"{key}_RPS", 0.0),
        cache_ttl_s=_float_env(f"{key}_CACHE_TTL_S", 0.0),
        forwarded_headers=frozenset((FORWARDED_HEADERS | extra_headers) - blocked),
    )


def rest_status() -> dict[str, dict[str, object]]:
    """Per-upstream configuration summary for /status (never includes keys)."""
    result: dict[str, dict[str, object]] = {}
    for name in upstream_names():
        try:
            upstream = get_upstream(name)
        except ValueError as exception:
            result[name] = {"configured": False, "detail": str(exception)}
            continue
        result[name] = {
            "configured": True,
            "base_url": upstream.config.base_url,
            "auth": upstream.config.api_key is not None,
            "cache_ttl_s": upstream.cache_ttl_s,
            "rps": upstream.rps,
        }
    return result


def log_rest_upstreams() -> None:
    for name, info in rest_status().items():
        if info["configured"]:
            LOGGER.info(
                "REST upstream %s -> %s (auth=%s, cache_ttl_s=%s, rps=%s)", name, info["base_url"], info["auth"], info["cache_ttl_s"], info["rps"]
            )
        else:
            LOGGER.warning("REST upstream %s is misconfigured: %s", name, info["detail"])


def _pacer(upstream: Upstream) -> Pacer:
    name = upstream.config.name
    if name not in _pacers:
        _pacers[name] = Pacer(upstream.rps)
    return _pacers[name]


def _forwarded_headers(request: Request, upstream: Upstream) -> dict[bytes, bytes]:
    # Bytes, not str: see brave._forwarded_headers.
    headers: dict[bytes, bytes] = {}
    for name, value in request.headers.items():
        lowered = name.lower()
        if lowered in upstream.forwarded_headers:
            headers[lowered.encode("latin-1")] = value.encode("latin-1")
    return headers


def _wants_fresh(request: Request) -> bool:
    directives = {part.strip().lower() for part in request.headers.get("cache-control", "").split(",")}
    return bool(directives & {"no-cache", "no-store"})


def _trace_response(response: httpx.Response) -> object | None:
    """The response in the form stored in the trace (and served from cache)."""
    if not response.content:
        return None
    content_type = response.headers.get("content-type")
    try:
        return response.json()
    except ValueError:
        pass
    try:
        # Strict UTF-8 only, so a cached copy re-encodes to the same bytes.
        return {"raw": response.content.decode("utf-8"), "content_type": content_type}
    except UnicodeDecodeError:
        return {"binary_bytes": len(response.content), "content_type": content_type}


def _cached_response(cached: object) -> Response | None:
    """Rebuild the client response from a cached trace, or None if it can't be."""
    try:
        payload = json.loads(cached.response_json)
    except TypeError, ValueError:
        return None
    if payload is None or (isinstance(payload, dict) and "binary_bytes" in payload):
        return None
    if isinstance(payload, dict) and set(payload) == RAW_KEYS:
        content, media_type = str(payload["raw"]).encode("utf-8"), payload["content_type"]
    else:
        content, media_type = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), "application/json"
    created_at: datetime = cached.created_at
    if created_at.tzinfo is None:
        # SQLite hands timestamps back naive; they are stored in UTC.
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = max(0, round((datetime.now(timezone.utc) - created_at).total_seconds()))
    return Response(
        content=content,
        status_code=cached.status_code or status.HTTP_200_OK,
        headers={"x-request-id": cached.request_id, "x-cache": "hit", "age": str(age)},
        media_type=media_type,
    )


@router.api_route("/rest/{name}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def rest_proxy(name: str, path: str, request: Request) -> Response:
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    name = name.lower()
    try:
        upstream = get_upstream(name)
    except ValueError as exception:
        # Not traced: the name is client-chosen and would become traces.provider.
        known = name in upstream_names()
        code = status.HTTP_503_SERVICE_UNAVAILABLE if known else status.HTTP_404_NOT_FOUND
        message = f"REST upstream {name!r} is misconfigured: {exception}" if known else f"Unknown REST upstream: {name[:32]!r}"
        return error_response(code, message, request_id)

    config = upstream.config
    method = request.method
    auth_headers, auth_params = build_auth(config)
    # Query params as a list of pairs, so repeated keys survive. A client's own
    # copy of the key parameter is dropped; the proxy supplies it.
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in auth_params]
    headers = _forwarded_headers(request, upstream)
    trace_payload: dict[str, object] = {"method": method, "path": path, "params": params}
    traced_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in sorted(headers.items()) if k.decode("latin-1") not in UNTRACED_HEADERS}
    if traced_headers:
        trace_payload["headers"] = traced_headers
    body: bytes | None = None
    if method != "GET":
        body = await request.body()
        if body:
            try:
                trace_payload["body"] = json.loads(body)
            except ValueError:
                trace_payload["body"] = body.decode("utf-8", errors="replace")
    valid_path = not any(segment in {".", ".."} for segment in path.split("/"))
    model = (path[:MAX_MODEL_LEN] or ROOT_PATH_MODEL) if valid_path else INVALID_PATH_MODEL

    async def trace(status_code: int | None, response_payload: object | None, error: str | None = None) -> None:
        await record_trace(
            request_id=request_id,
            provider=name,
            model=model,
            status_code=status_code,
            latency_ms=round((time.perf_counter() - started) * 1000),
            request_payload=trace_payload,
            response_payload=response_payload,
            error=error,
        )

    if not valid_path:
        # Refused rather than forwarded with the proxy's key attached.
        await trace(status.HTTP_400_BAD_REQUEST, None, f"invalid path {path[:MAX_MODEL_LEN]!r}")
        return error_response(status.HTTP_400_BAD_REQUEST, f"Invalid path: {path[:MAX_MODEL_LEN]!r}", request_id)

    cacheable = method == "GET" and upstream.cache_ttl_s > 0
    if cacheable and not _wants_fresh(request):
        cached = await lookup_rest_cache(provider=name, model=model, request_payload=trace_payload, max_age_s=upstream.cache_ttl_s)
        cached_response = _cached_response(cached) if cached is not None else None
        if cached is not None and cached_response is not None:
            await record_cache_hit(provider=name, model=model, cache_type="response", cached_trace_id=cached.id, request_payload=trace_payload)
            return cached_response

    for header, value in auth_headers.items():
        headers[header.lower().encode("latin-1")] = value.encode("utf-8")
    url = f"{config.base_url}/{quote(path, safe=PATH_SAFE)}" if path else config.base_url
    client: httpx.AsyncClient = request.app.state.http_client
    pacer = _pacer(upstream)

    response: httpx.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        # Every attempt takes its own slot, so retries are paced too.
        await pacer.acquire()
        try:
            response = await client.request(
                method, url, params=[*params, *auth_params.items()], headers=headers, content=body, timeout=upstream.timeout_s
            )
        except httpx.TimeoutException as exception:
            await trace(None, None, str(exception))
            return error_response(status.HTTP_504_GATEWAY_TIMEOUT, "Upstream request timed out", request_id)
        except httpx.HTTPError as exception:
            if attempt < MAX_RETRIES and method in IDEMPOTENT_METHODS:
                delay = compute_delay(attempt, None)
                LOGGER.warning("Retry %d/%d for %s/%s after %.1fs (error=%s)", attempt + 1, MAX_RETRIES, name, model, delay, exception)
                await asyncio.sleep(delay)
                continue
            await trace(None, None, str(exception))
            return error_response(status.HTTP_502_BAD_GATEWAY, "Upstream request failed", request_id)

        retry_after = response.headers.get("retry-after")
        throttled = response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        if throttled:
            # Backpressure: hold queued calls until the upstream's window resets.
            pacer.block(parse_retry_after(retry_after) or 1.0)
        retryable = throttled or (response.status_code in RETRYABLE_STATUSES and method in IDEMPOTENT_METHODS)
        if retryable and attempt < MAX_RETRIES:
            delay = compute_delay(attempt, retry_after)
            LOGGER.warning("Retry %d/%d for %s/%s after %.1fs (status=%d)", attempt + 1, MAX_RETRIES, name, model, delay, response.status_code)
            await asyncio.sleep(delay)
            continue
        break

    assert response is not None
    await trace(
        response.status_code, _trace_response(response), None if response.status_code < 400 else f"{name} returned HTTP {response.status_code}"
    )

    resp_headers = {k: v for k, v in response.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS}
    resp_headers["x-request-id"] = request_id
    if cacheable:
        resp_headers["x-cache"] = "miss"
    # The client gets the upstream's bytes back untouched.
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=resp_headers,
        media_type=response.headers.get("content-type"),
    )
