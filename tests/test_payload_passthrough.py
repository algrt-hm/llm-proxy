from llmproxy.providers import build_embeddings_url
from llmproxy.schemas import ChatCompletionRequest


def _dump(extra: dict | None = None) -> dict:
    base = {
        "model": "openrouter/moonshotai/kimi-k2.5",
        "messages": [{"role": "user", "content": "hi"}],
    }
    if extra:
        base.update(extra)
    req = ChatCompletionRequest(**base)
    return req.model_dump(exclude_none=True)


def test_reasoning_disabled_passthrough():
    """reasoning:{enabled:false} survives schema → model_dump for HTTP providers."""
    payload = _dump({"reasoning": {"enabled": False}})
    assert payload["reasoning"] == {"enabled": False}


def test_reasoning_enabled_passthrough():
    """reasoning:{enabled:true} survives schema → model_dump."""
    payload = _dump({"reasoning": {"enabled": True}})
    assert payload["reasoning"] == {"enabled": True}


def test_reasoning_with_budget_passthrough():
    """reasoning with budget survives schema → model_dump."""
    payload = _dump({"reasoning": {"enabled": True, "budget": 500}})
    assert payload["reasoning"] == {"enabled": True, "budget": 500}


def test_no_reasoning_field():
    """Without reasoning param, it does not appear in model_dump."""
    payload = _dump()
    assert "reasoning" not in payload


def test_extra_fields_preserved():
    """Arbitrary extra fields are preserved by extra='allow'."""
    payload = _dump({"logprobs": True, "top_logprobs": 5})
    assert payload["logprobs"] is True
    assert payload["top_logprobs"] == 5


# --- build_embeddings_url tests ---


def test_build_embeddings_url_plain_base():
    """Base URL without suffix gets /embeddings appended."""
    assert build_embeddings_url("https://api.openai.com/v1") == (
        "https://api.openai.com/v1/embeddings"
    )


def test_build_embeddings_url_already_has_embeddings():
    """Base URL ending in /embeddings is returned as-is."""
    assert build_embeddings_url("https://api.openai.com/v1/embeddings") == (
        "https://api.openai.com/v1/embeddings"
    )


def test_build_embeddings_url_strips_chat_completions():
    """Base URL ending in /chat/completions has that suffix replaced."""
    assert build_embeddings_url("https://api.openai.com/v1/chat/completions") == (
        "https://api.openai.com/v1/embeddings"
    )


def test_build_embeddings_url_trailing_slash():
    """Trailing slashes are stripped before processing."""
    assert build_embeddings_url("https://api.openai.com/v1/") == (
        "https://api.openai.com/v1/embeddings"
    )
