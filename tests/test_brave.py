import json
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request

from llmproxy import brave as brave_module


def _build_request(
    client: httpx.AsyncClient,
    query: str,
    headers: dict[str, str] | None = None,
    *,
    method: str = "GET",
    body: bytes = b"",
) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    raw_headers = [(key.lower().encode("utf-8"), value.encode("utf-8")) for key, value in (headers or {}).items()]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": "/brave/res/v1/llm/context",
        "raw_path": b"/brave/res/v1/llm/context",
        "query_string": query.encode("utf-8"),
        "headers": raw_headers,
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(http_client=client)),
    }
    return Request(scope, receive=_receive)


@pytest.fixture
def traces(monkeypatch):
    recorded: list[dict] = []

    async def fake_record_trace(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(brave_module, "record_trace", fake_record_trace)
    monkeypatch.setenv("BRAVE_API_KEY", "server-key")
    monkeypatch.delenv("BRAVE_BASE_URL", raising=False)
    # Unpaced, and fresh per test so a 429 in one test cannot delay the next.
    monkeypatch.setattr(brave_module, "pacer", brave_module.Pacer(0))
    return recorded


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_forwards_query_and_injects_key(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"grounding": {"generic": []}}, headers={"X-RateLimit-Remaining": "1, 999"})

    async with _client(handler) as client:
        request = _build_request(
            client,
            "q=how+deep&freshness=pw&x-subscription-token=client-key",
            {"X-Subscription-Token": "client-key", "X-Loc-Country": "GB", "Authorization": "Bearer nope"},
        )
        response = await brave_module.brave_proxy("llm/context", request)

    assert response.status_code == 200
    assert json.loads(response.body) == {"grounding": {"generic": []}}
    assert response.headers["x-ratelimit-remaining"] == "1, 999"
    assert "x-request-id" in response.headers

    (upstream,) = seen
    assert str(upstream.url).startswith("https://api.search.brave.com/res/v1/llm/context?")
    assert upstream.url.params.get("q") == "how deep"
    assert upstream.url.params.get("freshness") == "pw"
    assert "x-subscription-token" not in upstream.url.params
    assert upstream.headers["x-subscription-token"] == "server-key"
    assert upstream.headers["x-loc-country"] == "GB"
    assert "authorization" not in upstream.headers

    (trace,) = traces
    assert trace["provider"] == "brave"
    assert trace["model"] == "llm/context"
    assert trace["status_code"] == 200
    assert trace["request_payload"] == {"path": "llm/context", "params": [("q", "how deep"), ("freshness", "pw")]}
    assert "server-key" not in json.dumps(trace["request_payload"])


@pytest.mark.asyncio
async def test_unconfigured_returns_503_without_calling(traces, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream called without a key")

    async with _client(handler) as client:
        response = await brave_module.brave_proxy("llm/context", _build_request(client, "q=x"))

    assert response.status_code == 503
    assert "BRAVE_API_KEY" in json.loads(response.body)["error"]["message"]
    assert traces[0]["status_code"] == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../web/search", "llm/../../etc", "LLM/context", "llm//context", ""])
async def test_invalid_path_refused(traces, path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream called for an invalid path")

    async with _client(handler) as client:
        response = await brave_module.brave_proxy(path, _build_request(client, "q=x"))

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_upstream_error_passed_through_and_traced(traces, monkeypatch):
    monkeypatch.setattr(brave_module, "MAX_RETRIES", 0)
    body = {"type": "ErrorResponse", "error": {"code": "RATE_LIMITED", "detail": "slow down", "status": 429}}

    async with _client(lambda request: httpx.Response(429, json=body)) as client:
        response = await brave_module.brave_proxy("llm/context", _build_request(client, "q=x"))

    assert response.status_code == 429
    assert json.loads(response.body) == body
    assert traces[0]["status_code"] == 429
    assert "429" in traces[0]["error"]


@pytest.mark.asyncio
async def test_retries_retryable_status(traces, monkeypatch):
    monkeypatch.setattr(brave_module, "compute_delay", lambda attempt, retry_after: 0)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={"ok": len(calls)})

    async with _client(handler) as client:
        response = await brave_module.brave_proxy("llm/context", _build_request(client, "q=x"))

    assert response.status_code == 200
    assert len(calls) == 2
    assert len(traces) == 1


@pytest.mark.asyncio
async def test_base_url_override(traces, monkeypatch):
    monkeypatch.setenv("BRAVE_BASE_URL", "http://stub.local/res/v1/")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert seen == ["http://stub.local/res/v1/web/search?q=x"]


@pytest.mark.asyncio
async def test_non_ascii_header_forwarded_as_sent(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        response = await brave_module.brave_proxy("web/search", _build_request(client, "q=x", {"X-Loc-City": "São Paulo"}))

    assert response.status_code == 200
    assert dict(seen[0].headers.raw)[b"x-loc-city"] == "São Paulo".encode()


@pytest.mark.asyncio
async def test_client_accept_replaces_default(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await brave_module.brave_proxy("web/search", _build_request(client, "q=x", {"Accept": "*/*"}))
        await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert seen[0].headers.get_list("accept") == ["*/*"]
    assert seen[1].headers.get_list("accept") == ["application/json"]


@pytest.mark.asyncio
async def test_uses_brave_timeout_not_client_default(traces, monkeypatch):
    monkeypatch.setattr(brave_module, "TIMEOUT_S", 7.0)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=900) as client:
        await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert seen[0].extensions["timeout"] == {"connect": 7.0, "read": 7.0, "write": 7.0, "pool": 7.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../web/search", "x" * 300, "a/" * 100 + "a"])
async def test_invalid_path_not_recorded_as_model(traces, path):
    async with _client(lambda request: httpx.Response(200, json={})) as client:
        response = await brave_module.brave_proxy(path, _build_request(client, "q=x"))

    assert response.status_code == 400
    assert traces[0]["model"] == "invalid-path"
    assert traces[0]["request_payload"]["path"] == path


@pytest.mark.asyncio
async def test_post_body_forwarded(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    body = json.dumps({"q": "how deep", "count": 3}).encode("utf-8")
    async with _client(handler) as client:
        request = _build_request(client, "", {"Content-Type": "application/json"}, method="POST", body=body)
        response = await brave_module.brave_proxy("llm/context", request)

    assert response.status_code == 200
    assert seen[0].method == "POST"
    assert seen[0].content == body
    assert seen[0].headers["content-type"] == "application/json"
    assert traces[0]["request_payload"] == {"path": "llm/context", "params": [], "method": "POST", "body": {"q": "how deep", "count": 3}}


@pytest.mark.asyncio
async def test_non_json_body_passed_through_unwrapped(traces, monkeypatch):
    monkeypatch.setattr(brave_module, "MAX_RETRIES", 0)
    html = b"<html>502 Bad Gateway</html>"

    async with _client(lambda request: httpx.Response(502, content=html, headers={"content-type": "text/html"})) as client:
        response = await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert response.status_code == 502
    assert response.body == html
    assert response.headers["content-type"].startswith("text/html")
    assert traces[0]["response_payload"] == {"raw": html.decode()}


@pytest.mark.asyncio
async def test_bodiless_status_stays_bodiless(traces):
    async with _client(lambda request: httpx.Response(204)) as client:
        response = await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert response.status_code == 204
    assert response.body == b""


@pytest.mark.asyncio
async def test_pacer_spaces_calls_and_blocks_after_429(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(brave_module.asyncio, "sleep", fake_sleep)
    pacer = brave_module.Pacer(2)  # one call per 0.5s

    await pacer.acquire()
    await pacer.acquire()
    await pacer.acquire()
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.5, abs=0.05)
    assert sleeps[1] == pytest.approx(1.0, abs=0.05)

    pacer.block(5)
    await pacer.acquire()
    assert sleeps[2] == pytest.approx(5.0, abs=0.05)


@pytest.mark.asyncio
async def test_429_uses_ratelimit_reset_when_no_retry_after(traces, monkeypatch):
    delays: list[str | None] = []

    def fake_delay(attempt, retry_after):
        delays.append(retry_after)
        return 0

    monkeypatch.setattr(brave_module, "compute_delay", fake_delay)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={}, headers={"X-RateLimit-Reset": "1, 1419704"})
        return httpx.Response(200, json={})

    blocked: list[float] = []
    monkeypatch.setattr(brave_module.pacer, "block", blocked.append)

    async with _client(handler) as client:
        response = await brave_module.brave_proxy("web/search", _build_request(client, "q=x"))

    assert response.status_code == 200
    assert delays == ["1"]
    assert blocked == [1.0]
