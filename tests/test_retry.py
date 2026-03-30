from llmproxy.retry import parse_gemini_retry_after


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
