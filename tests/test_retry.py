from unittest.mock import patch

from llmproxy.retry import MAX_BACKOFF_S, compute_delay, parse_gemini_retry_after


def test_parse_gemini_retry_after_from_message_hint():
    error = "429 RESOURCE_EXHAUSTED. Please retry in 52.753648905s."
    parsed = parse_gemini_retry_after(error)
    assert parsed is not None
    assert abs(parsed - 52.753648905) < 1e-9


def test_parse_gemini_retry_after_from_retry_delay_field():
    error = "{'error': {'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '53s'}]}}"
    assert parse_gemini_retry_after(error) == 53.0


def test_parse_gemini_retry_after_returns_none_without_hint():
    assert parse_gemini_retry_after("network timeout") is None


# --- compute_delay ---


def test_compute_delay_retry_after_not_capped():
    """retry-after values above MAX_BACKOFF_S must not be capped."""
    with patch("llmproxy.retry.random.uniform", return_value=0.0):
        delay = compute_delay(0, "60")
    assert delay == 60.0


def test_compute_delay_backoff_capped():
    """Exponential backoff (no retry-after) is capped at MAX_BACKOFF_S."""
    with patch("llmproxy.retry.random.uniform", return_value=0.0):
        delay = compute_delay(10, None)  # 2^10 = 1024, way over cap
    assert delay == MAX_BACKOFF_S


def test_compute_delay_includes_jitter():
    """Delay should include jitter."""
    with patch("llmproxy.retry.random.uniform", return_value=0.5):
        delay = compute_delay(0, None)
    # backoff = 1.0, jitter = 0.5
    assert delay == 1.5
