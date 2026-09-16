"""Read the account's configured Anthropic rate limits from the Admin API.

Anthropic exposes the limits the console shows at
``GET /v1/organizations/rate_limits``, so the proxy can read them at startup
instead of carrying hardcoded numbers that drift whenever the account's tier
changes.

Two things make this endpoint awkward, and both are handled here by degrading
to the static table in ``ratelimit.py`` rather than failing startup:

* It is part of the **Admin API**, so it needs an Admin API key
  (``sk-ant-admin01-...``) or an organization-scoped key.  An ordinary
  ``sk-ant-api...`` inference key gets 401, and the Admin API is not available
  to individual (non-organization) accounts at all.  Keys are tried in order --
  ``ANTHROPIC_API_KEY`` first (the variable Anthropic's own SDKs read for admin
  calls), then ``ANTHROPIC_ADMIN_API_KEY`` -- and which one works is settled by
  asking rather than by inspecting the key's prefix, since admin-capable keys
  are not reliably distinguishable by shape.
* The limits it returns are org-wide.  Workspace overrides live on a separate
  endpoint and are not read here.

Docs: https://platform.claude.com/docs/en/manage-claude/rate-limits-api
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from os import getenv
from pathlib import Path
from typing import Any

import httpx

from .constants import ANTHROPIC_VERSION, PACKAGE_NAME

LOGGER = logging.getLogger(PACKAGE_NAME)

RATE_LIMITS_URL = "https://api.anthropic.com/v1/organizations/rate_limits"
CACHE_MAX_AGE = timedelta(hours=24)
DEFAULT_CACHE_PATH = Path("./llmproxy_anthropic_limits.json")
FETCH_TIMEOUT_S = 30.0
MAX_PAGES = 20

# Limiter types we care about.  The proxy's sliding window meters requests and
# *input* tokens, so output_tokens_per_minute is read but unused.
RPM_TYPE = "requests_per_minute"
TPM_TYPE = "input_tokens_per_minute"

# model id/alias -> {limiter type: value}
LimitMap = dict[str, dict[str, int]]


def admin_key_candidates() -> list[tuple[str, str]]:
    """Keys to try against the Admin API, in order, as ``(env var, key)``.

    ``ANTHROPIC_API_KEY`` is tried first: that is the variable Anthropic's own
    SDKs read for Admin API calls, so on an account where the inference key is
    admin-capable nothing else needs configuring.  ``ANTHROPIC_ADMIN_API_KEY``
    is the fallback for the usual case here, where ``ANTHROPIC_API_KEY`` holds
    an ordinary inference key that the Admin API refuses.

    Whether a key works is settled by asking, not by inspecting its prefix:
    admin-capable keys are not reliably distinguishable by shape (a Claude
    Enterprise admin key is ``sk-ant-api01-…``, and an organization-scoped
    personal or service-account key is accepted too).
    """
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_ADMIN_API_KEY"):
        key = getenv(name, "").strip()
        if key and key not in seen:
            seen.add(key)
            candidates.append((name, key))
    return candidates


def _parse_groups(data: list[dict[str, Any]]) -> LimitMap:
    """Flatten ``model_group`` entries into a model id -> limits mapping.

    Each group lists every model id and alias that shares its limits, so the
    result supports exact lookup with no prefix guessing.
    """
    limits: LimitMap = {}
    for group in data:
        if not isinstance(group, dict) or group.get("group_type") != "model_group":
            continue
        models = group.get("models")
        if not isinstance(models, list):
            continue

        parsed: dict[str, int] = {}
        for entry in group.get("limits") or []:
            if not isinstance(entry, dict):
                continue
            name, value = entry.get("type"), entry.get("value")
            if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool):
                parsed[name] = value
        if not parsed:
            continue

        for model in models:
            if isinstance(model, str) and model:
                limits[model] = dict(parsed)
    return limits


async def fetch_anthropic_limits(api_key: str) -> LimitMap | None:
    """Fetch org-level model rate limits with one key.

    Returns ``None`` when the key itself was refused (401/403), so the caller
    knows it is worth trying another one.  Returns ``{}`` when the key was
    accepted but nothing usable came back, or on a non-auth failure — neither
    of which another key would fix.
    """
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    limits: LimitMap = {}

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
            params: dict[str, str] = {"group_type": "model_group"}
            for _ in range(MAX_PAGES):
                response = await client.get(RATE_LIMITS_URL, headers=headers, params=params)
                if response.status_code in (401, 403):
                    LOGGER.debug(
                        "Anthropic rate-limits API refused a key (%d): %s",
                        response.status_code,
                        response.text[:200],
                    )
                    return None
                response.raise_for_status()
                payload = response.json()

                limits.update(_parse_groups(payload.get("data") or []))

                next_page = payload.get("next_page")
                if not next_page:
                    break
                params = {"group_type": "model_group", "page": next_page}
            else:
                LOGGER.warning("Anthropic rate-limits API paginated past %d pages; truncating", MAX_PAGES)
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exception:
        LOGGER.warning("Failed to fetch Anthropic rate limits: %s; falling back to the static table", exception)
        return {}

    return limits


async def fetch_with_fallback() -> LimitMap:
    """Try each configured key in turn, stopping at the first one accepted.

    Only an auth refusal moves on to the next key; a network failure is not
    key-specific, so another key would fail the same way.
    """
    candidates = admin_key_candidates()
    if not candidates:
        LOGGER.info("No Anthropic API key configured; using the static rate-limit table")
        return {}

    refused: list[str] = []
    for env_name, key in candidates:
        result = await fetch_anthropic_limits(key)
        if result is None:
            refused.append(env_name)
            continue
        if result:
            LOGGER.info("Loaded live Anthropic rate limits for %d models using %s", len(result), env_name)
        return result

    LOGGER.warning(
        "Anthropic Admin API refused every configured key (%s); using the static rate-limit table. "
        "Set ANTHROPIC_ADMIN_API_KEY to an Admin API key (sk-ant-admin01-…) to enable live limits: "
        "https://platform.claude.com/docs/en/manage-claude/admin-api-keys",
        ", ".join(refused),
    )
    return {}


def _cache_path() -> Path:
    raw = getenv("LLM_PROXY_ANTHROPIC_LIMITS_CACHE")
    if raw:
        return Path(raw)
    return DEFAULT_CACHE_PATH


def load_limits_cache(path: Path) -> LimitMap | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError, OSError:
        return None

    fetched_at = data.get("fetched_at")
    if not fetched_at:
        return None
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if datetime.now(UTC) - ts > CACHE_MAX_AGE:
        return None

    models = data.get("models")
    if not isinstance(models, dict) or not models:
        return None

    results: LimitMap = {}
    for model, entry in models.items():
        if isinstance(entry, dict):
            results[model] = {k: v for k, v in entry.items() if isinstance(v, int) and not isinstance(v, bool)}
    return results or None


def save_limits_cache(path: Path, limits: LimitMap) -> None:
    data = {"fetched_at": datetime.now(UTC).isoformat(), "models": limits}
    try:
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exception:
        LOGGER.warning("Failed to write Anthropic rate-limit cache: %s", exception)


async def get_or_fetch_anthropic_limits(path: Path | None = None) -> LimitMap:
    """Return live Anthropic limits, from disk cache when fresh.

    Returns ``{}`` when no admin key is configured or the fetch fails, which
    leaves the static table in ``ratelimit.py`` in charge.
    """
    if path is None:
        path = _cache_path()

    cached = load_limits_cache(path)
    if cached is not None:
        LOGGER.info("Using cached Anthropic rate limits (%d models, from %s)", len(cached), path)
        return cached

    LOGGER.info("Fetching Anthropic rate limits from the Admin API...")
    limits = await fetch_with_fallback()
    if limits:
        save_limits_cache(path, limits)
    return limits
