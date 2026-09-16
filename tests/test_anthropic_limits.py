import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from llmproxy.anthropic_limits import (
    RPM_TYPE,
    TPM_TYPE,
    _parse_groups,
    admin_key_candidates,
    fetch_anthropic_limits,
    fetch_with_fallback,
    get_or_fetch_anthropic_limits,
    load_limits_cache,
    save_limits_cache,
)
from llmproxy.ratelimit import AnthropicRateLimiter

# Response shape taken from the Rate Limits API docs.
SAMPLE_PAGE = {
    "data": [
        {
            "type": "rate_limit",
            "group_type": "model_group",
            "models": ["claude-opus-5"],
            "limits": [
                {"type": "requests_per_minute", "value": 4000},
                {"type": "input_tokens_per_minute", "value": 10000000},
                {"type": "output_tokens_per_minute", "value": 800000},
            ],
        },
        {
            "type": "rate_limit",
            "group_type": "model_group",
            "models": ["claude-opus-4-5", "claude-opus-4-5-20251101", "claude-opus-4-8"],
            "limits": [
                {"type": "requests_per_minute", "value": 4000},
                {"type": "input_tokens_per_minute", "value": 10000000},
            ],
        },
        {
            "type": "rate_limit",
            "group_type": "batch",
            "models": None,
            "limits": [{"type": "enqueued_batch_requests", "value": 500000}],
        },
    ],
    "next_page": None,
}


class _FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text or json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class _FakeClient:
    """Stands in for httpx.AsyncClient, serving queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self._responses.pop(0)


def _patch_client(client):
    return patch("llmproxy.anthropic_limits.httpx.AsyncClient", lambda *a, **kw: client)


# --- Response parsing ---


def test_parse_groups_flattens_every_alias():
    """Each model id in a group gets the group's limits."""
    parsed = _parse_groups(SAMPLE_PAGE["data"])
    assert parsed["claude-opus-5"] == {
        "requests_per_minute": 4000,
        "input_tokens_per_minute": 10000000,
        "output_tokens_per_minute": 800000,
    }
    # Every alias in the second group resolves identically.
    for alias in ("claude-opus-4-5", "claude-opus-4-5-20251101", "claude-opus-4-8"):
        assert parsed[alias]["requests_per_minute"] == 4000


def test_parse_groups_skips_non_model_groups():
    """batch/files/etc. carry no model list and must not leak in."""
    parsed = _parse_groups(SAMPLE_PAGE["data"])
    assert "enqueued_batch_requests" not in json.dumps(parsed)
    assert set(parsed) == {
        "claude-opus-5",
        "claude-opus-4-5",
        "claude-opus-4-5-20251101",
        "claude-opus-4-8",
    }


def test_parse_groups_ignores_malformed_entries():
    parsed = _parse_groups(
        [
            {"group_type": "model_group", "models": "not-a-list", "limits": []},
            {"group_type": "model_group", "models": ["ok"], "limits": [{"type": "requests_per_minute", "value": 5}]},
            {"group_type": "model_group", "models": ["bad"], "limits": [{"type": "x", "value": "nope"}]},
            "not-a-dict",
        ]
    )
    assert parsed == {"ok": {"requests_per_minute": 5}}


# --- Fetching ---


@pytest.mark.asyncio
async def test_fetch_sends_admin_headers_and_filters_group_type():
    client = _FakeClient([_FakeResponse(SAMPLE_PAGE)])
    with _patch_client(client):
        result = await fetch_anthropic_limits("sk-ant-admin-test")

    assert result["claude-opus-5"]["requests_per_minute"] == 4000
    call = client.calls[0]
    assert call["headers"]["x-api-key"] == "sk-ant-admin-test"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["params"]["group_type"] == "model_group"


@pytest.mark.asyncio
async def test_fetch_returns_none_on_401():
    """A refused key returns None, so the caller knows to try another."""
    client = _FakeClient([_FakeResponse({"error": "nope"}, status_code=401, text="admin key required")])
    with _patch_client(client):
        assert await fetch_anthropic_limits("sk-ant-api03-ordinary") is None


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_transport_error():
    class _Boom(_FakeClient):
        async def get(self, url, headers=None, params=None):
            raise httpx.ConnectError("unreachable")

    with _patch_client(_Boom([])):
        # {} not None: another key would hit the same network failure.
        assert await fetch_anthropic_limits("sk-ant-admin-test") == {}


@pytest.mark.asyncio
async def test_fetch_follows_next_page():
    page1 = {
        "data": [
            {
                "group_type": "model_group",
                "models": ["claude-sonnet-5"],
                "limits": [{"type": "requests_per_minute", "value": 100}],
            }
        ],
        "next_page": "page_2",
    }
    page2 = {
        "data": [
            {
                "group_type": "model_group",
                "models": ["claude-haiku-4-5"],
                "limits": [{"type": "requests_per_minute", "value": 200}],
            }
        ],
        "next_page": None,
    }
    client = _FakeClient([_FakeResponse(page1), _FakeResponse(page2)])
    with _patch_client(client):
        result = await fetch_anthropic_limits("sk-ant-admin-test")

    assert set(result) == {"claude-sonnet-5", "claude-haiku-4-5"}
    assert client.calls[1]["params"]["page"] == "page_2"


# --- Key ordering and fallback ---


def test_candidates_try_inference_key_first(monkeypatch):
    """ANTHROPIC_API_KEY is what Anthropic's SDKs read, so it goes first."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-inference")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "key-admin")
    assert admin_key_candidates() == [
        ("ANTHROPIC_API_KEY", "key-inference"),
        ("ANTHROPIC_ADMIN_API_KEY", "key-admin"),
    ]


def test_candidates_deduplicate_identical_keys(monkeypatch):
    """Same value in both vars must not be tried twice."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "same")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "same")
    assert admin_key_candidates() == [("ANTHROPIC_API_KEY", "same")]


def test_candidates_skip_blank_and_missing(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    assert admin_key_candidates() == []


def test_candidates_accept_ordinary_looking_key(monkeypatch):
    """No prefix sniffing: an sk-ant-api key is still worth asking with.

    A Claude Enterprise admin key is sk-ant-api01-shaped, and an
    organization-scoped key is accepted too, so shape proves nothing.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ordinary")
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    assert admin_key_candidates() == [("ANTHROPIC_API_KEY", "sk-ant-api03-ordinary")]


@pytest.mark.asyncio
async def test_fallback_uses_admin_key_after_inference_key_refused(monkeypatch):
    """The documented case: inference key 401s, admin key then works."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ordinary")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-real")
    client = _FakeClient(
        [
            _FakeResponse({"error": "nope"}, status_code=401, text="admin key required"),
            _FakeResponse(SAMPLE_PAGE),
        ]
    )
    with _patch_client(client):
        result = await fetch_with_fallback()

    assert result["claude-opus-5"]["requests_per_minute"] == 4000
    assert client.calls[0]["headers"]["x-api-key"] == "sk-ant-api03-ordinary"
    assert client.calls[1]["headers"]["x-api-key"] == "sk-ant-admin01-real"


@pytest.mark.asyncio
async def test_fallback_stops_at_first_accepted_key(monkeypatch):
    """An admin-capable ANTHROPIC_API_KEY means the fallback is never tried."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-admin01-works")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-unused")
    client = _FakeClient([_FakeResponse(SAMPLE_PAGE)])
    with _patch_client(client):
        result = await fetch_with_fallback()

    assert result
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_fallback_returns_empty_when_all_keys_refused(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ordinary")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin01-also-bad")
    client = _FakeClient(
        [
            _FakeResponse({}, status_code=401, text="nope"),
            _FakeResponse({}, status_code=403, text="nope"),
        ]
    )
    with _patch_client(client):
        assert await fetch_with_fallback() == {}
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_fallback_does_not_try_second_key_on_network_error(monkeypatch):
    """A transport failure isn't key-specific, so don't burn the other key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "b")

    class _Boom(_FakeClient):
        async def get(self, url, headers=None, params=None):
            self.calls.append({"url": url, "headers": headers, "params": params})
            raise httpx.ConnectError("unreachable")

    client = _Boom([])
    with _patch_client(client):
        assert await fetch_with_fallback() == {}
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_fallback_without_any_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)

    def _explode(*a, **kw):
        raise AssertionError("must not hit the network with no key configured")

    with patch("llmproxy.anthropic_limits.httpx.AsyncClient", _explode):
        assert await fetch_with_fallback() == {}


# --- Caching ---


def test_cache_round_trip(tmp_path):
    path = tmp_path / "limits.json"
    limits = {"claude-opus-5": {"requests_per_minute": 4000, "input_tokens_per_minute": 10000000}}
    save_limits_cache(path, limits)
    assert load_limits_cache(path) == limits


def test_cache_expires(tmp_path):
    path = tmp_path / "limits.json"
    stale = datetime.now(UTC) - timedelta(hours=25)
    path.write_text(json.dumps({"fetched_at": stale.isoformat(), "models": {"claude-opus-5": {"x": 1}}}))
    assert load_limits_cache(path) is None


def test_cache_missing_or_corrupt(tmp_path):
    assert load_limits_cache(tmp_path / "absent.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert load_limits_cache(corrupt) is None


@pytest.mark.asyncio
async def test_get_or_fetch_skips_network_without_any_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_ADMIN_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _explode(*a, **kw):
        raise AssertionError("must not hit the network with no key configured")

    with patch("llmproxy.anthropic_limits.httpx.AsyncClient", _explode):
        assert await get_or_fetch_anthropic_limits(tmp_path / "limits.json") == {}


@pytest.mark.asyncio
async def test_get_or_fetch_uses_fresh_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_ADMIN_API_KEY", "sk-ant-admin-test")
    path = tmp_path / "limits.json"
    save_limits_cache(path, {"claude-opus-5": {"requests_per_minute": 1}})

    def _explode(*a, **kw):
        raise AssertionError("must not refetch while the cache is fresh")

    with patch("llmproxy.anthropic_limits.httpx.AsyncClient", _explode):
        assert await get_or_fetch_anthropic_limits(path) == {"claude-opus-5": {"requests_per_minute": 1}}


# --- Limiter integration ---


def test_live_limits_override_static_table():
    """A live entry wins over the hardcoded claude-opus prefix row."""
    lim = AnthropicRateLimiter()
    before = lim._limits_for("claude-opus-5")
    lim.apply_live_limits(
        {"claude-opus-5": {RPM_TYPE: 4000, TPM_TYPE: 10_000_000}},
        rpm_key=RPM_TYPE,
        tpm_key=TPM_TYPE,
    )
    assert lim._limits_for("claude-opus-5") == (4000, 10_000_000)
    assert before != (4000, 10_000_000)


def test_live_limits_are_exact_match_only():
    """Live limits enumerate every alias, so no prefix fallthrough."""
    lim = AnthropicRateLimiter()
    lim.apply_live_limits({"claude-opus-5": {RPM_TYPE: 4000, TPM_TYPE: 10_000_000}}, rpm_key=RPM_TYPE, tpm_key=TPM_TYPE)
    # An unlisted model still resolves via the static prefix table.
    assert lim._limits_for("claude-opus-4-8") == lim.model_limits["claude-opus"]


def test_live_limits_fill_missing_limiter_from_defaults():
    """A group with no input_tokens_per_minute keeps the default TPM."""
    lim = AnthropicRateLimiter()
    lim.apply_live_limits({"claude-sonnet-5": {RPM_TYPE: 777}}, rpm_key=RPM_TYPE, tpm_key=TPM_TYPE)
    assert lim._limits_for("claude-sonnet-5") == (777, lim.default_tpm)


def test_apply_live_limits_reports_count_and_empty_is_noop():
    lim = AnthropicRateLimiter()
    baseline = lim._limits_for("claude-opus-5")
    assert lim.apply_live_limits({}, rpm_key=RPM_TYPE, tpm_key=TPM_TYPE) == 0
    assert lim._limits_for("claude-opus-5") == baseline
