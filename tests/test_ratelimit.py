import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest

from llmproxy.ratelimit import (
    RPM,
    TPM,
    WINDOW,
    GeminiRateLimiter,
    OpenAIRateLimiter,
    RateLimiter,
    _EVICT_INTERVAL,
)


@pytest.fixture
def limiter():
    return GeminiRateLimiter()


@pytest.mark.asyncio
async def test_acquire_returns_slot(limiter):
    slot = await limiter.acquire("model-a")
    assert isinstance(slot, list)
    assert len(slot) == 2
    assert slot[1] == 0


@pytest.mark.asyncio
async def test_record_updates_slot(limiter):
    slot = await limiter.acquire("model-a")
    limiter.record(slot, 500)
    assert slot[1] == 500


@pytest.mark.asyncio
async def test_rpm_under_limit_no_delay(limiter):
    """Acquiring fewer than RPM slots should not delay."""
    start = time.monotonic()
    for _ in range(RPM):
        await limiter.acquire("model-a")
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_rpm_blocks_at_limit(limiter):
    """The RPM+1 request should block until the window slides."""
    window = limiter._get_window("model-a")
    now = time.monotonic()
    for _ in range(RPM):
        window.append([now - WINDOW + 0.1, 0])

    start = time.monotonic()
    slot = await limiter.acquire("model-a")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05
    assert slot is not None


@pytest.mark.asyncio
async def test_tpm_blocks_at_limit(limiter):
    """Requests should block when TPM budget is exhausted."""
    window = limiter._get_window("model-a")
    now = time.monotonic()
    window.append([now - WINDOW + 0.1, TPM])

    start = time.monotonic()
    slot = await limiter.acquire("model-a")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05
    assert slot is not None


@pytest.mark.asyncio
async def test_window_expiry_frees_slots(limiter):
    """Entries older than WINDOW should be purged, freeing capacity."""
    window = limiter._get_window("model-a")
    now = time.monotonic()
    for _ in range(RPM):
        window.append([now - WINDOW - 1.0, 100])

    start = time.monotonic()
    slot = await limiter.acquire("model-a")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert slot is not None


@pytest.mark.asyncio
async def test_independent_models(limiter):
    """Rate limits are tracked independently per model."""
    window_a = limiter._get_window("model-a")
    now = time.monotonic()
    for _ in range(RPM):
        window_a.append([now - WINDOW + 0.15, 0])

    start = time.monotonic()
    slot = await limiter.acquire("model-b")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5
    assert slot is not None


@pytest.mark.asyncio
async def test_concurrent_acquires(limiter):
    """Multiple concurrent acquires should not exceed RPM."""
    slots = await asyncio.gather(*(limiter.acquire("model-a") for _ in range(RPM)))
    assert len(slots) == RPM
    window = limiter._get_window("model-a")
    assert len(window) == RPM


@pytest.mark.asyncio
async def test_slot_stays_zero_on_error(limiter):
    """If record is never called, the slot stays at 0 tokens (error case)."""
    slot = await limiter.acquire("model-a")
    assert slot[1] == 0
    window = limiter._get_window("model-a")
    assert slot in list(window)


# --- Eviction of stale model state ---


@pytest.mark.asyncio
async def test_evict_stale_models():
    """Models with empty windows should be evicted after the eviction interval."""
    limiter = GeminiRateLimiter()
    limiter._windows["gone-model"] = deque()
    limiter._locks["gone-model"] = asyncio.Lock()
    limiter._last_eviction = time.monotonic() - _EVICT_INTERVAL - 1.0

    await limiter.acquire("active-model")

    assert "gone-model" not in limiter._windows
    assert "gone-model" not in limiter._locks
    assert "active-model" in limiter._windows


@pytest.mark.asyncio
async def test_evict_preserves_active_models(limiter):
    """Active models (non-empty windows) should not be evicted."""
    await limiter.acquire("active-model")
    limiter._last_eviction = time.monotonic() - _EVICT_INTERVAL - 1.0

    await limiter.acquire("active-model")
    assert "active-model" in limiter._windows
    assert "active-model" in limiter._locks


@pytest.mark.asyncio
async def test_evict_skipped_before_interval(limiter):
    """Eviction should not run before the interval elapses."""
    limiter._windows["empty-model"] = deque()
    limiter._locks["empty-model"] = asyncio.Lock()
    # _last_eviction is recent (set in __init__)

    await limiter.acquire("other-model")

    # empty-model should still be present — interval hasn't elapsed
    assert "empty-model" in limiter._windows


# --- Retry + limiter integration ---


@pytest.mark.asyncio
async def test_retry_acquires_slot_per_attempt():
    """Each Gemini retry attempt should acquire its own rate-limit slot."""
    from llmproxy import app as app_module

    limiter = GeminiRateLimiter()
    acquire_calls: list[list] = []
    original_acquire = limiter.acquire

    async def tracking_acquire(model):
        slot = await original_acquire(model)
        acquire_calls.append(slot)
        return slot

    limiter.acquire = tracking_acquire

    mock_response = AsyncMock()
    mock_response.text = "hello"
    mock_response.usage_metadata = None

    call_count = 0

    async def fake_generate(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        return mock_response

    with (
        patch.object(app_module, "gemini_rate_limiter", limiter),
        patch.object(
            app_module,
            "generate_gemini_response",
            side_effect=fake_generate,
        ),
        patch.object(app_module, "record_trace", new_callable=AsyncMock),
        patch.object(
            app_module,
            "build_gemini_contents",
            return_value=("sys", [{"role": "user", "parts": ["hi"]}]),
        ),
        patch.object(app_module, "build_gemini_config", return_value={}),
        patch.object(app_module, "compute_delay", return_value=0.0),
    ):
        result = await app_module._handle_gemini_request(
            request_id="test-123",
            started=time.perf_counter(),
            incoming_payload={
                "messages": [{"role": "user", "content": "hi"}],
            },
            trace_payload={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "gemini-test",
                "stream": False,
            },
            upstream_model="gemini-test",
            api_key="fake-key",
            stream=False,
        )

    # 2 slots: one for the failed attempt, one for the successful retry
    assert len(acquire_calls) == 2
    # First slot stays at 0 (failed attempt — record never called)
    assert acquire_calls[0][1] == 0
    assert result.status_code == 200


@pytest.mark.asyncio
async def test_retry_honors_gemini_retry_hint():
    """Gemini retry hint should override short exponential backoff."""
    from llmproxy import app as app_module

    limiter = GeminiRateLimiter()
    mock_sleep = AsyncMock()

    with (
        patch.object(app_module, "gemini_rate_limiter", limiter),
        patch.object(
            app_module,
            "generate_gemini_response",
            side_effect=RuntimeError("RESOURCE_EXHAUSTED"),
        ),
        patch.object(app_module, "record_trace", new_callable=AsyncMock),
        patch.object(
            app_module,
            "build_gemini_contents",
            return_value=("sys", [{"role": "user", "parts": ["hi"]}]),
        ),
        patch.object(app_module, "build_gemini_config", return_value={}),
        patch.object(app_module, "MAX_RETRIES", 1),
        patch.object(app_module, "compute_delay", return_value=1.0),
        patch.object(app_module, "parse_gemini_retry_after", return_value=53.0),
        patch.object(app_module.asyncio, "sleep", new=mock_sleep),
    ):
        result = await app_module._handle_gemini_request(
            request_id="test-123",
            started=time.perf_counter(),
            incoming_payload={
                "messages": [{"role": "user", "content": "hi"}],
            },
            trace_payload={
                "messages": [{"role": "user", "content": "hi"}],
                "model": "gemini-test",
                "stream": False,
            },
            upstream_model="gemini-test",
            api_key="fake-key",
            stream=False,
        )

    mock_sleep.assert_awaited_once_with(53.0)
    assert result.status_code == 502


# --- Per-model limits and prefix matching ---


def test_per_model_limits_exact_match():
    """_limits_for returns the exact-match entry when available."""
    lim = RateLimiter(
        "test",
        default_rpm=100,
        default_tpm=50_000,
        model_limits={
            "gpt-4o": (5_000, 800_000),
        },
    )
    assert lim._limits_for("gpt-4o") == (5_000, 800_000)


def test_per_model_limits_prefix_match():
    """Date-suffixed model matches the base prefix entry."""
    lim = RateLimiter(
        "test",
        default_rpm=100,
        default_tpm=50_000,
        model_limits={
            "gpt-4o": (5_000, 800_000),
        },
    )
    assert lim._limits_for("gpt-4o-2024-11-20") == (5_000, 800_000)


def test_per_model_limits_longest_prefix_wins():
    """Longest prefix wins when multiple prefixes match."""
    lim = RateLimiter(
        "test",
        default_rpm=100,
        default_tpm=50_000,
        model_limits={
            "gpt-4.1": (5_000, 800_000),
            "gpt-4.1-mini": (5_000, 4_000_000),
        },
    )
    assert lim._limits_for("gpt-4.1-mini-2025-04-14") == (5_000, 4_000_000)


def test_per_model_limits_fallback_to_default():
    """Unknown model falls back to default limits."""
    lim = RateLimiter(
        "test",
        default_rpm=100,
        default_tpm=50_000,
        model_limits={
            "gpt-4o": (5_000, 800_000),
        },
    )
    assert lim._limits_for("totally-unknown-model") == (100, 50_000)


def test_openai_limiter_known_models():
    """OpenAIRateLimiter returns correct limits for known models."""
    lim = OpenAIRateLimiter()
    assert lim._limits_for("gpt-4o") == (5_000, 800_000)
    assert lim._limits_for("o4-mini") == (5_000, 4_000_000)
    assert lim._limits_for("gpt-4.1-mini") == (5_000, 4_000_000)
    # Unknown model gets defaults
    assert lim._limits_for("ft:gpt-custom:org:suffix") == (5_000, 800_000)


@pytest.mark.asyncio
async def test_per_model_tpm_blocking():
    """Per-model TPM limit blocks when budget is exhausted."""
    lim = RateLimiter(
        "test",
        default_rpm=10_000,
        default_tpm=100,
        model_limits={
            "small-model": (10_000, 100),
        },
    )
    window = lim._get_window("small-model")
    now = time.monotonic()
    # Fill the TPM budget with a single entry that hasn't expired yet
    window.append([now - WINDOW + 0.1, 100])

    start = time.monotonic()
    slot = await lim.acquire("small-model")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05
    assert slot is not None
