import asyncio
import logging
import time
from collections import deque
from os import getenv

from .constants import PACKAGE_NAME

LOGGER = logging.getLogger(PACKAGE_NAME)


def _int_env(name: str, default: int) -> int:
    raw = getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        LOGGER.warning("Invalid %s=%r, using default %d", name, raw, default)
        return default


RPM = 22  # ~10% below Gemini free-tier limit (25 RPM)
TPM = 900_000  # ~10% below Gemini free-tier limit (1M TPM)
WINDOW = 60.0

# How often (in seconds) to sweep stale per-model state.
_EVICT_INTERVAL = WINDOW

# OpenAI per-model rate limits (rpm, tpm).
# Models not listed here fall back to the OpenAI defaults.
OPENAI_MODEL_LIMITS: dict[str, tuple[int, int]] = {
    "gpt-5.1": (5_000, 2_000_000),
    "gpt-5-mini": (5_000, 4_000_000),
    "gpt-5-nano": (5_000, 4_000_000),
    "gpt-4.1": (5_000, 800_000),
    "gpt-4.1-mini": (5_000, 4_000_000),
    "gpt-4.1-nano": (5_000, 4_000_000),
    "o3": (5_000, 800_000),
    "o4-mini": (5_000, 4_000_000),
    "gpt-4o": (5_000, 800_000),
    "gpt-4o-realtime-preview": (5_000, 800_000),
    "text-embedding-3-small": (5_000, 5_000_000),
    "text-embedding-3-large": (5_000, 5_000_000),
}


class RateLimiter:
    """Sliding-window rate limiter (per upstream model).

    Tracks both request count (RPM) and input tokens (TPM) over a 60-second
    window.  Token tracking is retroactive: the request that pushes past the
    TPM limit still goes through, but subsequent requests wait until budget
    frees up.

    Subclasses (e.g. GeminiRateLimiter, OpenAIRateLimiter) configure provider
    label, default limits, and optional per-model overrides.
    """

    def __init__(
        self,
        provider: str,
        *,
        default_rpm: int,
        default_tpm: int,
        model_limits: dict[str, tuple[int, int]] | None = None,
    ) -> None:
        self.provider = provider
        self.default_rpm = default_rpm
        self.default_tpm = default_tpm
        self.model_limits = model_limits or {}
        # model -> deque of [timestamp, prompt_tokens] slots
        self._windows: dict[str, deque[list[float | int]]] = {}
        # model -> asyncio.Lock
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_eviction: float = time.monotonic()
        # model -> monotonic timestamp until which acquire() must wait
        # (set by block_model after upstream 429)
        self._blocked_until: dict[str, float] = {}

    def _limits_for(self, model: str) -> tuple[int, int]:
        """Return ``(rpm, tpm)`` for *model*.

        Checks exact match first, then longest-prefix match against the
        ``model_limits`` keys (handles date suffixes like
        ``gpt-4o-2024-11-20`` → ``gpt-4o``).  Falls back to defaults.
        """
        if model in self.model_limits:
            return self.model_limits[model]
        best_key: str | None = None
        for key in self.model_limits:
            if model.startswith(key) and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key is not None:
            return self.model_limits[best_key]
        return (self.default_rpm, self.default_tpm)

    def _get_lock(self, model: str) -> asyncio.Lock:
        if model not in self._locks:
            self._locks[model] = asyncio.Lock()
        return self._locks[model]

    def _get_window(self, model: str) -> deque[list[float | int]]:
        if model not in self._windows:
            self._windows[model] = deque()
        return self._windows[model]

    def _purge(self, window: deque[list[float | int]], now: float) -> None:
        cutoff = now - WINDOW
        while window and window[0][0] <= cutoff:
            window.popleft()

    def _maybe_evict(self, now: float, *, exclude: str) -> None:
        """Periodically remove models whose windows are empty.

        Skips models whose lock is currently held: a coroutine inside
        ``acquire()`` for that model still references the old lock/window
        objects, and evicting them would orphan its bookkeeping.
        """
        if now - self._last_eviction < _EVICT_INTERVAL:
            return
        self._last_eviction = now
        stale = [m for m, w in self._windows.items() if not w and m != exclude and not (m in self._locks and self._locks[m].locked())]
        for m in stale:
            del self._windows[m]
            self._locks.pop(m, None)
        expired_blocks = [m for m, until in self._blocked_until.items() if until <= now]
        for m in expired_blocks:
            del self._blocked_until[m]

    def block_model(self, model: str, seconds: float) -> None:
        """Block *model* for *seconds* after an upstream 429.

        All pending ``acquire()`` calls for *model* will wait until the
        block expires.  If a longer block is already active it is kept.
        """
        until = time.monotonic() + seconds
        current = self._blocked_until.get(model, 0)
        self._blocked_until[model] = max(current, until)
        LOGGER.info(
            "%s backpressure: model=%s blocked for %.1fs",
            self.provider,
            model,
            seconds,
        )

    async def acquire(self, model: str, estimated_tokens: int = 0) -> list[float | int]:
        """Wait until rate limits allow a new request, then reserve a slot.

        *estimated_tokens* is a rough pre-send token estimate used for the
        TPM check so that concurrent requests don't all bypass the limit.
        The caller should later call :meth:`record` to fill in the actual
        prompt-token count.

        Returns a mutable ``[timestamp, estimated_tokens]`` slot already
        appended to the window.
        """
        rpm_limit, tpm_limit = self._limits_for(model)

        while True:
            # Re-fetch each iteration: eviction may have replaced the lock and
            # window objects while we slept outside the lock.
            lock = self._get_lock(model)
            window = self._get_window(model)
            async with lock:
                now = time.monotonic()
                self._purge(window, now)
                self._maybe_evict(now, exclude=model)

                sleep_time = 0.0

                # Backpressure: upstream 429 blocked this model.
                blocked_until = self._blocked_until.get(model, 0)
                if now < blocked_until:
                    sleep_time = blocked_until - now

                rpm = len(window)
                tpm = sum(int(s[1]) for s in window)

                if rpm >= rpm_limit:
                    # Wait until oldest entry expires
                    sleep_time = max(sleep_time, window[0][0] - (now - WINDOW))

                if tpm + estimated_tokens >= tpm_limit:
                    # Walk entries to find when enough tokens expire
                    remaining = tpm + estimated_tokens - tpm_limit
                    for s in window:
                        remaining -= int(s[1])
                        if remaining < 0:
                            sleep_time = max(sleep_time, s[0] - (now - WINDOW))
                            break

                if sleep_time <= 0:
                    slot: list[float | int] = [now, estimated_tokens]
                    window.append(slot)
                    return slot

                LOGGER.info(
                    "%s rate limit: model=%s waiting %.1fs (rpm=%d/%d tpm=%d+%d/%d)",
                    self.provider,
                    model,
                    sleep_time,
                    rpm,
                    rpm_limit,
                    tpm,
                    estimated_tokens,
                    tpm_limit,
                )

            # Sleep outside the lock so other models aren't blocked
            await asyncio.sleep(sleep_time)

    def record(self, slot: list[float | int], prompt_tokens: int) -> None:
        """Update a previously acquired slot with actual prompt-token usage."""
        slot[1] = prompt_tokens


# Tier-dependent Anthropic limits; override via env when the account tier changes.
ANTHROPIC_RPM = _int_env("LLM_PROXY_ANTHROPIC_RPM", 50)
ANTHROPIC_TPM = _int_env("LLM_PROXY_ANTHROPIC_TPM", 30_000)
ANTHROPIC_MODEL_LIMITS: dict[str, tuple[int, int]] = {
    "claude-opus": (ANTHROPIC_RPM, _int_env("LLM_PROXY_ANTHROPIC_OPUS_TPM", 500_000)),
    "claude-sonnet": (ANTHROPIC_RPM, _int_env("LLM_PROXY_ANTHROPIC_SONNET_TPM", 30_000)),
    "claude-haiku": (ANTHROPIC_RPM, _int_env("LLM_PROXY_ANTHROPIC_HAIKU_TPM", 50_000)),
}


class GeminiRateLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__("Gemini", default_rpm=RPM, default_tpm=TPM)


class OpenAIRateLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(
            "OpenAI",
            default_rpm=5_000,
            default_tpm=800_000,
            model_limits=OPENAI_MODEL_LIMITS,
        )


class AnthropicRateLimiter(RateLimiter):
    def __init__(self) -> None:
        super().__init__(
            "Anthropic",
            default_rpm=ANTHROPIC_RPM,
            default_tpm=ANTHROPIC_TPM,
            model_limits=ANTHROPIC_MODEL_LIMITS,
        )


def estimate_tokens(payload: dict) -> int:
    """Rough token estimate from message content (~4 chars per token).

    Good enough for pre-checking TPM budgets in the rate limiter so
    concurrent requests don't all bypass the limit simultaneously.
    """
    total_chars = 0
    for msg in payload.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total_chars += len(part.get("text", ""))
    return max(1, total_chars // 4)
