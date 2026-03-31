import logging
import re
from email.utils import parsedate_to_datetime
from os import getenv

from .constants import PACKAGE_NAME

RETRYABLE_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = int(getenv("LLM_PROXY_MAX_RETRIES", "2"))
RETRY_BASE_DELAY_S = float(getenv("LLM_PROXY_RETRY_BASE_DELAY_S", "1.0"))
MAX_DELAY_S = 30.0
LOGGER = logging.getLogger(PACKAGE_NAME)

_GEMINI_RETRY_PATTERNS = (
    # Example: "Please retry in 53.021757137s."
    re.compile(r"please retry in (?P<seconds>\d+(?:\.\d+)?)s", re.IGNORECASE),
    # Example: "'retryDelay': '53s'"
    re.compile(
        r"retrydelay[\"']?\s*[:=]\s*[\"'](?P<seconds>\d+(?:\.\d+)?)s[\"']",
        re.IGNORECASE,
    ),
)


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from datetime import datetime, timezone

        dt = parsedate_to_datetime(value)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:  # noqa: BLE001
        return None


def parse_gemini_retry_after(error: Exception | str | None) -> float | None:
    """Extract Gemini retry delay seconds from SDK exception text."""
    if error is None:
        return None
    text = str(error)
    for pattern in _GEMINI_RETRY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return max(0.0, float(match.group("seconds")))
        except ValueError:
            continue
    return None


def compute_delay(attempt: int, retry_after: str | None) -> float:
    backoff = RETRY_BASE_DELAY_S * (2**attempt)
    parsed = parse_retry_after(retry_after)
    if parsed is not None:
        delay = max(parsed, backoff)
    else:
        delay = backoff
    return min(delay, MAX_DELAY_S)
