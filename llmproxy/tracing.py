import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from .constants import PACKAGE_NAME
from .db import CacheHit, SessionLocal, Trace

LOGGER = logging.getLogger(PACKAGE_NAME)


@dataclass
class TraceStats:
    """Counters for trace/cache-hit commit failures, exposed via /status."""

    trace_failures: int = field(default=0)
    cache_hit_failures: int = field(default=0)


trace_stats = TraceStats()


def _to_json(value: object | None) -> str | None:
    """Object to JSON

    Note this does belong here rather than in utility.py as it's run here

    Args:
        value (object | None): object to JSONify

    Returns:
        str | None: None if the input is None, otherwise a JSON string representation of the input
    """

    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        LOGGER.warning("Failed to serialize value to JSON, storing repr", exc_info=True)
        return json.dumps({"_repr": repr(value)}, ensure_ascii=True, separators=(",", ":"))


def _build_cache_key(*, provider: str, model: str, request_payload: object | None) -> str | None:
    """Cache key from the provider, model, and request payload

    Args:
        provider (str): model provider
        model (str): model name
        request_payload (object | None): API request payload

    Returns:
        str | None: returns None if request_payload is None, otherwise a SHA256 hash of the JSON
    """

    if request_payload is None:
        return None

    payload = {
        "provider": provider,
        "model": model,
        "request": request_payload,
    }

    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()


async def record_trace(
    *,
    request_id: str,
    provider: str,
    model: str,
    status_code: int | None,
    latency_ms: int | None,
    request_payload: object | None,
    response_payload: object | None,
    error: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    """Record a trace of the API request and response.

    Args:
        request_id: unique request identifier
        provider: model provider name
        model: model name
        status_code: HTTP status code of the response
        latency_ms: round-trip latency in milliseconds
        request_payload: API request payload (stored as JSON)
        response_payload: API response payload (stored as JSON)
        error: error message if the request failed
        idempotency_key: client-supplied idempotency key
    """

    # Generate key
    cache_key = _build_cache_key(
        provider=provider,
        model=model,
        request_payload=request_payload,
    )

    # Generate trace
    trace = Trace(
        request_id=request_id,
        provider=provider,
        model=model,
        status_code=status_code,
        latency_ms=latency_ms,
        request_json=_to_json(request_payload),
        response_json=_to_json(response_payload),
        error=error,
        idempotency_key=idempotency_key,
        cache_key=cache_key,
    )

    # Store trace (shielded so client disconnects don't cancel the DB write)
    try:
        await asyncio.shield(_commit_trace(trace))
    except asyncio.CancelledError:
        pass


async def _commit_trace(trace: Trace) -> None:
    try:
        async with SessionLocal() as session:
            session.add(trace)
            await session.commit()
    except Exception:
        trace_stats.trace_failures += 1
        LOGGER.exception("Failed to commit trace %s", trace.request_id)


async def record_cache_hit(
    *,
    provider: str,
    model: str,
    cache_type: str,
    cached_trace_id: int,
    request_payload: object | None,
) -> None:
    hit = CacheHit(
        provider=provider,
        model=model,
        cache_type=cache_type,
        cached_trace_id=cached_trace_id,
        request_json=_to_json(request_payload),
    )
    try:
        await asyncio.shield(_commit_cache_hit(hit))
    except asyncio.CancelledError:
        pass


async def _commit_cache_hit(hit: CacheHit) -> None:
    try:
        async with SessionLocal() as session:
            session.add(hit)
            await session.commit()
    except Exception:
        trace_stats.cache_hit_failures += 1
        LOGGER.exception("Failed to commit cache hit")


async def lookup_idempotency(key: str, *, provider: str, model: str) -> Trace | None:
    try:
        async with SessionLocal() as session:
            stmt = (
                select(Trace)
                .where(
                    Trace.idempotency_key == key,
                    Trace.provider == provider,
                    Trace.model == model,
                    Trace.status_code.isnot(None),
                    Trace.status_code >= 200,
                    Trace.status_code < 300,
                )
                .order_by(Trace.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
    except Exception:
        LOGGER.exception("Failed idempotency lookup for key=%s", key)
        return None


async def lookup_response_cache(*, provider: str, model: str, request_payload: object) -> Trace | None:
    cache_key = _build_cache_key(
        provider=provider,
        model=model,
        request_payload=request_payload,
    )
    if not cache_key:
        return None

    try:
        async with SessionLocal() as session:
            stmt = (
                select(Trace)
                .where(
                    Trace.cache_key == cache_key,
                    Trace.status_code.isnot(None),
                    Trace.status_code >= 200,
                    Trace.status_code < 300,
                    Trace.response_json.isnot(None),
                )
                .order_by(Trace.id.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            trace = result.scalar_one_or_none()

            if trace is None or not trace.response_json:
                return None

            try:
                payload = json.loads(trace.response_json)
            except json.JSONDecodeError:
                return None

            # Defensive guard: streaming traces are stored as {"raw": "..."}.
            if isinstance(payload, dict) and "raw" in payload:
                return None

            # Reject degenerate chat completions with null/empty content.
            if isinstance(payload, dict) and "choices" in payload:
                choices = payload.get("choices") or []
                if choices and all((c.get("message") or {}).get("content") in (None, "") for c in choices):
                    return None

            return trace
    except Exception:
        LOGGER.exception("Failed response cache lookup for cache_key=%s", cache_key)
        return None
