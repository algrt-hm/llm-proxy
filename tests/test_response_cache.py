import json
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from llmproxy import app as app_module
from llmproxy import tracing as tracing_module
from llmproxy.db import Base, Trace
from llmproxy.providers import ProviderConfig
from llmproxy.schemas import ChatCompletionRequest, EmbeddingRequest
from llmproxy.tracing import _build_cache_key, lookup_response_cache


async def _receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


def _build_request(
    headers: dict[str, str] | None = None, path: str = "/v1/chat/completions"
) -> Request:
    raw_headers = [
        (key.lower().encode("utf-8"), value.encode("utf-8"))
        for key, value in (headers or {}).items()
    ]
    app = SimpleNamespace(state=SimpleNamespace(http_client=None))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope, receive=_receive)


@pytest.mark.asyncio
async def test_default_cache_hit_returns_cached_response(monkeypatch):
    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    captured: dict[str, object] = {}

    async def fake_lookup_response_cache(
        *, provider: str, model: str, request_payload: object
    ):
        captured["provider"] = provider
        captured["model"] = model
        captured["request_payload"] = request_payload
        return SimpleNamespace(
            id=42,
            request_id="cached-request-id",
            status_code=200,
            response_json=json.dumps({"id": "cached", "object": "chat.completion"}),
        )

    cache_hit_captured: dict[str, object] = {}

    async def fake_record_cache_hit(**kwargs):
        cache_hit_captured.update(kwargs)

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "record_cache_hit", fake_record_cache_hit)

    payload = ChatCompletionRequest(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
    )
    response = await app_module.chat_completions(_build_request(), payload)
    body = json.loads(response.body)

    assert response.status_code == 200
    assert response.headers["x-cache"] == "hit"
    assert response.headers["x-request-id"] == "cached-request-id"
    assert body == {"id": "cached", "object": "chat.completion"}
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-4o-mini"
    assert captured["request_payload"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


@pytest.mark.asyncio
async def test_cache_false_disables_lookup(monkeypatch):
    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        raise AssertionError(
            "lookup_response_cache should not be called when cache=false"
        )

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key=None,
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = ChatCompletionRequest(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        cache=False,
    )
    response = await app_module.chat_completions(_build_request(), payload)
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["error"]["message"] == "OPENAI_API_KEY is not set"


@pytest.mark.asyncio
async def test_invalid_cache_value_returns_400_and_records_trace(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_record_trace(**kwargs):
        captured.update(kwargs)
        return None

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        raise AssertionError(
            "lookup_response_cache should not be called for invalid cache"
        )

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)

    payload = ChatCompletionRequest(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
        cache={"some": "object"},
    )
    response = await app_module.chat_completions(_build_request(), payload)
    body = json.loads(response.body)

    assert response.status_code == 400
    assert "Invalid 'cache' value" in body["error"]["message"]
    assert captured["provider"] == "openai"
    assert captured["model"] == "gpt-4o-mini"
    assert captured["request_payload"] == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
        "cache": {"some": "object"},
    }


@pytest.mark.asyncio
async def test_unknown_provider_trace_uses_normalized_model_and_payload(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_record_trace(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(
        app_module,
        "parse_model",
        lambda model: ("not-a-provider", model),  # noqa: ARG005
    )

    payload = ChatCompletionRequest(
        model="custom-prefix/my-model",
        messages=[{"role": "user", "content": "hello"}],
    )
    response = await app_module.chat_completions(_build_request(), payload)
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["error"]["message"] == "Unknown provider: not-a-provider"
    assert captured["provider"] == "not-a-provider"
    assert captured["model"] == "custom-prefix/my-model"
    assert captured["request_payload"] == {
        "model": "custom-prefix/my-model",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": False,
    }


# --- Idempotency cross-endpoint rejection ---


@pytest.mark.asyncio
async def test_idempotency_rejects_chat_response_on_embeddings_endpoint(monkeypatch):
    """A chat-shaped idempotency hit should not be served on the embeddings endpoint."""

    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    async def fake_lookup_idempotency(key, *, provider, model):  # noqa: ARG001
        return SimpleNamespace(
            id=99,
            request_id="chat-req-id",
            status_code=200,
            response_json=json.dumps(
                {"id": "chatcmpl-abc", "object": "chat.completion"}
            ),
        )

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key=None,
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_idempotency", fake_lookup_idempotency)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = EmbeddingRequest(model="openai/text-embedding-3-small", input="hello")
    req = _build_request(
        headers={"idempotency-key": "shared-key"}, path="/v1/embeddings"
    )
    response = await app_module.embeddings(req, payload)
    body = json.loads(response.body)

    # Should NOT return the cached chat response — falls through to API key check.
    assert response.status_code == 400
    assert "API_KEY" in body["error"]["message"]


@pytest.mark.asyncio
async def test_idempotency_rejects_embedding_response_on_chat_endpoint(monkeypatch):
    """An embedding-shaped idempotency hit should not be served on the chat endpoint."""

    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    async def fake_lookup_idempotency(key, *, provider, model):  # noqa: ARG001
        return SimpleNamespace(
            id=99,
            request_id="emb-req-id",
            status_code=200,
            response_json=json.dumps({"object": "list", "data": []}),
        )

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key=None,
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_idempotency", fake_lookup_idempotency)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = ChatCompletionRequest(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
    )
    req = _build_request(headers={"idempotency-key": "shared-key"})
    response = await app_module.chat_completions(req, payload)
    body = json.loads(response.body)

    # Should NOT return the cached embedding response — falls through to API key check.
    assert response.status_code == 400
    assert "API_KEY" in body["error"]["message"]


# --- Response cache cross-endpoint rejection ---


@pytest.mark.asyncio
async def test_response_cache_rejects_embedding_on_chat_endpoint(monkeypatch):
    """An embedding-shaped response cache hit should not be served on the chat endpoint."""

    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        return SimpleNamespace(
            id=77,
            request_id="emb-cached-id",
            status_code=200,
            response_json=json.dumps({"object": "list", "data": []}),
        )

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key=None,
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = ChatCompletionRequest(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "hello"}],
    )
    response = await app_module.chat_completions(_build_request(), payload)
    body = json.loads(response.body)

    # Should NOT return the cached embedding response — falls through to API key check.
    assert response.status_code == 400
    assert "API_KEY" in body["error"]["message"]


@pytest.mark.asyncio
async def test_response_cache_rejects_chat_on_embeddings_endpoint(monkeypatch):
    """A chat-shaped response cache hit should not be served on the embeddings endpoint."""

    async def fake_record_trace(**kwargs):  # noqa: ARG001
        return None

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        return SimpleNamespace(
            id=78,
            request_id="chat-cached-id",
            status_code=200,
            response_json=json.dumps(
                {"id": "chatcmpl-xyz", "object": "chat.completion"}
            ),
        )

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key=None,
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = EmbeddingRequest(model="openai/text-embedding-3-small", input="hello")
    req = _build_request(path="/v1/embeddings")
    response = await app_module.embeddings(req, payload)
    body = json.loads(response.body)

    # Should NOT return the cached chat response — falls through to API key check.
    assert response.status_code == 400
    assert "API_KEY" in body["error"]["message"]


# --- Empty embedding input ---


@pytest.mark.asyncio
async def test_empty_embedding_input_returns_400(monkeypatch):
    """Submitting input=[] to embeddings should return 400."""
    captured: dict[str, object] = {}

    async def fake_record_trace(**kwargs):
        captured.update(kwargs)
        return None

    async def fake_lookup_response_cache(**kwargs):  # noqa: ARG001
        return None

    def fake_get_provider_config(name: str) -> ProviderConfig:
        return ProviderConfig(
            name=name,
            api_key="fake-key",
            base_url="https://api.openai.com/v1",
            auth_header="Authorization",
            auth_prefix="Bearer",
            auth_query_param=None,
        )

    monkeypatch.setattr(app_module, "record_trace", fake_record_trace)
    monkeypatch.setattr(app_module, "lookup_response_cache", fake_lookup_response_cache)
    monkeypatch.setattr(app_module, "get_provider_config", fake_get_provider_config)

    payload = EmbeddingRequest(model="openai/text-embedding-3-small", input=[])
    req = _build_request(path="/v1/embeddings")
    response = await app_module.embeddings(req, payload)
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["error"]["message"] == "input must not be empty"
    assert captured["error"] == "input must not be empty"


# --- Degenerate response rejection ---


VALID_CHAT_RESPONSE = {
    "id": "test-1",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "Hello"}}
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

NULL_CONTENT_RESPONSE = {
    "id": "test-2",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "finish_reason": None, "message": {"role": "assistant", "content": None}}
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100},
}

EMPTY_CONTENT_RESPONSE = {
    "id": "test-3",
    "object": "chat.completion",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ""}}
    ],
    "usage": {"prompt_tokens": 100, "completion_tokens": 0, "total_tokens": 100},
}


async def _setup_cache_db(monkeypatch):
    """Create in-memory DB, patch SessionLocal, seed nothing yet."""
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    sf = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(tracing_module, "SessionLocal", sf)
    return sf


async def _seed_trace(session_factory, *, response, provider="openrouter", model="test-model"):
    req_payload = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    cache_key = _build_cache_key(provider=provider, model=model, request_payload=req_payload)
    trace = Trace(
        request_id="test-req",
        provider=provider,
        model=model,
        status_code=200,
        latency_ms=100.0,
        request_json=json.dumps(req_payload),
        response_json=json.dumps(response),
        cache_key=cache_key,
    )
    async with session_factory() as session:
        session.add(trace)
        await session.commit()
    return req_payload


@pytest.mark.asyncio
async def test_cache_rejects_null_content_response(monkeypatch):
    """A cached chat response with content=null must not be served."""
    sf = await _setup_cache_db(monkeypatch)
    req_payload = await _seed_trace(sf, response=NULL_CONTENT_RESPONSE)
    result = await lookup_response_cache(
        provider="openrouter", model="test-model", request_payload=req_payload
    )
    assert result is None


@pytest.mark.asyncio
async def test_cache_rejects_empty_content_response(monkeypatch):
    """A cached chat response with content='' must not be served."""
    sf = await _setup_cache_db(monkeypatch)
    req_payload = await _seed_trace(sf, response=EMPTY_CONTENT_RESPONSE)
    result = await lookup_response_cache(
        provider="openrouter", model="test-model", request_payload=req_payload
    )
    assert result is None


@pytest.mark.asyncio
async def test_cache_serves_valid_content_response(monkeypatch):
    """A cached chat response with actual content should be served."""
    sf = await _setup_cache_db(monkeypatch)
    req_payload = await _seed_trace(sf, response=VALID_CHAT_RESPONSE)
    result = await lookup_response_cache(
        provider="openrouter", model="test-model", request_payload=req_payload
    )
    assert result is not None
    body = json.loads(result.response_json)
    assert body["choices"][0]["message"]["content"] == "Hello"
