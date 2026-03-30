import asyncio
import logging
import time
from collections import deque

LOGGER = logging.getLogger("llmproxy")

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
        """Periodically remove models whose windows are empty."""
        if now - self._last_eviction < _EVICT_INTERVAL:
            return
        self._last_eviction = now
        stale = [m for m, w in self._windows.items() if not w and m != exclude]
        for m in stale:
            del self._windows[m]
            self._locks.pop(m, None)

    async def acquire(self, model: str) -> list[float | int]:
        """Wait until rate limits allow a new request, then reserve a slot.

        Returns a mutable ``[timestamp, 0]`` slot already appended to the
        window.  The caller should later call :meth:`record` to fill in the
        actual prompt-token count.
        """
        lock = self._get_lock(model)
        window = self._get_window(model)
        rpm_limit, tpm_limit = self._limits_for(model)

        while True:
            async with lock:
                now = time.monotonic()
                self._purge(window, now)
                self._maybe_evict(now, exclude=model)

                rpm = len(window)
                tpm = sum(int(s[1]) for s in window)

                sleep_time = 0.0

                if rpm >= rpm_limit:
                    # Wait until oldest entry expires
                    sleep_time = max(sleep_time, window[0][0] - (now - WINDOW))

                if tpm >= tpm_limit:
                    # Walk entries to find when enough tokens expire
                    remaining = tpm - tpm_limit
                    for s in window:
                        remaining -= int(s[1])
                        if remaining < 0:
                            sleep_time = max(sleep_time, s[0] - (now - WINDOW))
                            break

                if sleep_time <= 0:
                    slot: list[float | int] = [now, 0]
                    window.append(slot)
                    return slot

                LOGGER.info(
                    "%s rate limit: model=%s waiting %.1fs (rpm=%d/%d tpm=%d/%d)",
                    self.provider,
                    model,
                    sleep_time,
                    rpm,
                    rpm_limit,
                    tpm,
                    tpm_limit,
                )

            # Sleep outside the lock so other models aren't blocked
            await asyncio.sleep(sleep_time)

    def record(self, slot: list[float | int], prompt_tokens: int) -> None:
        """Update a previously acquired slot with actual prompt-token usage."""
        slot[1] = prompt_tokens


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
