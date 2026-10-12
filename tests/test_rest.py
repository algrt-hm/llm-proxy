import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from llmproxy import rest as rest_module
from llmproxy import tracing as tracing_module
from llmproxy.db import Base, CacheHit, Trace


def _build_request(
    client: httpx.AsyncClient,
    query: str = "",
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
        "path": "/rest/tfl/x",
        "raw_path": b"/rest/tfl/x",
        "query_string": query.encode("utf-8"),
        "headers": raw_headers,
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "app": SimpleNamespace(state=SimpleNamespace(http_client=client)),
    }
    return Request(scope, receive=_receive)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def tfl(monkeypatch):
    """A TfL-shaped upstream: key sent as the ``app_key`` query parameter."""
    monkeypatch.setenv("LLM_PROXY_REST_UPSTREAMS", "tfl, github")
    monkeypatch.setenv("TFL_BASE_URL", "https://api.tfl.gov.uk/")
    monkeypatch.setenv("TFL_API_KEY", "server-key")
    monkeypatch.setenv("TFL_AUTH_QUERY", "app_key")
    monkeypatch.setenv("GITHUB_BASE_URL", "https://api.github.com")
    monkeypatch.setenv("GITHUB_API_KEY", "gh-token")
    monkeypatch.setenv("GITHUB_FORWARD_HEADERS", "X-GitHub-Api-Version, Authorization")
    for name in ("TFL_CACHE_TTL_S", "TFL_RPS", "TFL_TIMEOUT_S", "TFL_AUTH_HEADER", "TFL_AUTH_PREFIX"):
        monkeypatch.delenv(name, raising=False)
    # Fresh per test so a 429 in one test cannot delay the next.
    monkeypatch.setattr(rest_module, "_pacers", {})


@pytest.fixture
def traces(monkeypatch, tfl):
    recorded: list[dict] = []

    async def fake_record_trace(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(rest_module, "record_trace", fake_record_trace)
    return recorded


@pytest_asyncio.fixture
async def db(monkeypatch, tfl):
    """Real in-memory trace DB, with caching switched on for tfl."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(tracing_module, "SessionLocal", session_factory)
    monkeypatch.setenv("TFL_CACHE_TTL_S", "60")
    yield session_factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_forwards_path_and_injects_query_key(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"journeys": []}, headers={"X-Total-Count": "0", "Set-Cookie": "a=b"})

    path = "Journey/JourneyResults/SE13 7NZ/to/910GCNTBW"
    async with _client(handler) as client:
        request = _build_request(
            client, "date=20261010&mode=tube&mode=dlr&app_key=client-key", {"Authorization": "Bearer nope", "Accept": "application/json"}
        )
        response = await rest_module.rest_proxy("tfl", path, request)

    assert response.status_code == 200
    assert json.loads(response.body) == {"journeys": []}
    assert response.headers["x-total-count"] == "0"
    assert "set-cookie" not in response.headers
    assert "x-cache" not in response.headers  # caching is off by default

    (upstream,) = seen
    assert upstream.url.raw_path.startswith(b"/Journey/JourneyResults/SE13%207NZ/to/910GCNTBW?")
    assert upstream.url.params.get_list("mode") == ["tube", "dlr"]
    assert upstream.url.params.get_list("app_key") == ["server-key"]
    assert "authorization" not in upstream.headers

    (trace,) = traces
    assert trace["provider"] == "tfl"
    assert trace["model"] == path
    assert trace["request_payload"] == {
        "method": "GET",
        "path": path,
        "params": [("date", "20261010"), ("mode", "tube"), ("mode", "dlr")],
        "headers": {"accept": "application/json"},
    }
    assert "server-key" not in json.dumps(trace["request_payload"])


@pytest.mark.asyncio
async def test_header_auth_and_extra_forwarded_headers(traces):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 1})

    async with _client(handler) as client:
        request = _build_request(
            client,
            headers={"X-GitHub-Api-Version": "2022-11-28", "Authorization": "Bearer client", "Content-Type": "application/json"},
            method="POST",
            body=b'{"title":"t"}',
        )
        response = await rest_module.rest_proxy("github", "repos/a/b/issues", request)

    assert response.status_code == 201
    (upstream,) = seen
    # The proxy's key, never the client's, even though Authorization was listed.
    assert upstream.headers["authorization"] == "Bearer gh-token"
    assert upstream.headers["x-github-api-version"] == "2022-11-28"
    assert upstream.content == b'{"title":"t"}'
    payload = traces[0]["request_payload"]
    assert payload["body"] == {"title": "t"}
    assert "authorization" not in payload["headers"]
    assert "gh-token" not in json.dumps(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "env", "expected"),
    [
        ("nope", {}, 404),
        ("openai", {"LLM_PROXY_REST_UPSTREAMS": "openai", "OPENAI_BASE_URL": "https://x.test"}, 503),
        ("tfl", {"TFL_BASE_URL": ""}, 503),
    ],
)
async def test_unknown_or_misconfigured_upstream(traces, monkeypatch, name, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream called")

    async with _client(handler) as client:
        response = await rest_module.rest_proxy(name, "x", _build_request(client))

    assert response.status_code == expected
    assert traces == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["../x", "a/../../etc", "a/./b"])
async def test_dot_segments_refused(traces, path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream called for an invalid path")

    async with _client(handler) as client:
        response = await rest_module.rest_proxy("tfl", path, _build_request(client))

    assert response.status_code == 400
    assert traces[0]["model"] == "invalid-path"


@pytest.mark.asyncio
async def test_get_retried_on_503_but_post_is_not(traces, monkeypatch):
    monkeypatch.setattr(rest_module, "compute_delay", lambda attempt, retry_after: 0)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200, json={})

    async with _client(handler) as client:
        response = await rest_module.rest_proxy("tfl", "Line/Mode/tube/Status", _build_request(client))
        assert response.status_code == 200
        assert len(calls) == 2

        calls.clear()
        response = await rest_module.rest_proxy("tfl", "things", _build_request(client, method="POST", body=b"{}"))
        assert response.status_code == 503
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_post_retried_on_429(traces, monkeypatch):
    monkeypatch.setattr(rest_module, "compute_delay", lambda attempt, retry_after: 0)
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(429 if len(calls) == 1 else 200, json={}, headers={"Retry-After": "0"})

    async with _client(handler) as client:
        response = await rest_module.rest_proxy("tfl", "things", _build_request(client, method="POST", body=b"{}"))

    assert response.status_code == 200
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_timeout_returns_504_without_retry(traces):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    async with _client(handler) as client:
        response = await rest_module.rest_proxy("tfl", "x", _build_request(client))

    assert response.status_code == 504
    assert len(calls) == 1
    assert traces[0]["status_code"] is None


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_and_expiry(db):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"n": len(calls), "name": "Lewisham é"})

    async with _client(handler) as client:
        first = await rest_module.rest_proxy("tfl", "StopPoint/1", _build_request(client, "a=1"))
        second = await rest_module.rest_proxy("tfl", "StopPoint/1", _build_request(client, "a=1"))
        other_query = await rest_module.rest_proxy("tfl", "StopPoint/1", _build_request(client, "a=2"))
        assert first.headers["x-cache"] == "miss"
        assert second.headers["x-cache"] == "hit"
        assert second.headers["x-request-id"] == first.headers["x-request-id"]
        assert json.loads(second.body) == {"n": 1, "name": "Lewisham é"}
        assert second.headers["content-type"] == "application/json"
        assert other_query.headers["x-cache"] == "miss"
        assert len(calls) == 2

        async with db() as session:
            hits = (await session.execute(select(CacheHit))).scalars().all()
            assert [(hit.provider, hit.model, hit.cache_type) for hit in hits] == [("tfl", "StopPoint/1", "response")]
            # Age every trace past the 60s TTL.
            await session.execute(update(Trace).values(created_at=datetime.now(timezone.utc) - timedelta(seconds=120)))
            await session.commit()

        stale = await rest_module.rest_proxy("tfl", "StopPoint/1", _build_request(client, "a=1"))
        assert stale.headers["x-cache"] == "miss"
        assert json.loads(stale.body)["n"] == 3


@pytest.mark.asyncio
async def test_no_cache_header_bypasses_and_refreshes(db):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"n": len(calls)})

    async with _client(handler) as client:
        await rest_module.rest_proxy("tfl", "x", _build_request(client))
        fresh = await rest_module.rest_proxy("tfl", "x", _build_request(client, headers={"Cache-Control": "no-cache"}))
        after = await rest_module.rest_proxy("tfl", "x", _build_request(client))

    assert json.loads(fresh.body) == {"n": 2}
    assert after.headers["x-cache"] == "hit"
    assert json.loads(after.body) == {"n": 2}
    assert "cache-control" not in calls[1].headers


@pytest.mark.asyncio
async def test_text_cached_verbatim_errors_posts_and_binary_not_cached(db):
    calls: list[httpx.Request] = []
    xml = "<a>é</a>".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/xml":
            return httpx.Response(200, content=xml, headers={"Content-Type": "application/xml"})
        if request.url.path == "/png":
            return httpx.Response(200, content=b"\x89PNG\xff\x00", headers={"Content-Type": "image/png"})
        if request.url.path == "/missing":
            return httpx.Response(404, json={"message": "no"})
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await rest_module.rest_proxy("tfl", "xml", _build_request(client))
        hit = await rest_module.rest_proxy("tfl", "xml", _build_request(client))
        assert hit.headers["x-cache"] == "hit"
        assert hit.body == xml
        assert hit.headers["content-type"].startswith("application/xml")

        for path, method in (("png", "GET"), ("missing", "GET"), ("post", "POST")):
            calls.clear()
            await rest_module.rest_proxy("tfl", path, _build_request(client, method=method))
            again = await rest_module.rest_proxy("tfl", path, _build_request(client, method=method))
            assert len(calls) == 2, path
            assert again.headers.get("x-cache") != "hit"
        assert again.body == b"{}"


def test_rest_status_hides_keys_and_reports_misconfiguration(tfl, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_REST_UPSTREAMS", "tfl,Bad-Name,anthropic")
    monkeypatch.setenv("TFL_CACHE_TTL_S", "300")
    result = rest_module.rest_status()

    assert result["tfl"] == {"configured": True, "base_url": "https://api.tfl.gov.uk", "auth": True, "cache_ttl_s": 300.0, "rps": 0.0}
    assert result["bad-name"]["configured"] is False
    assert result["anthropic"]["configured"] is False
    assert "server-key" not in json.dumps(result)
