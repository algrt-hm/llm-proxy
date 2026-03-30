import asyncio
import json
import logging
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from os import getenv
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx
from google import genai

from .providers import PROVIDERS, ProviderConfig, build_auth, get_provider_config

LOGGER = logging.getLogger("llmproxy")
ANTHROPIC_VERSION = "2023-06-01"
CACHE_MAX_AGE = timedelta(hours=24)
DEFAULT_CACHE_PATH = Path("./llmproxy_validation.json")
VALIDATION_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class ProviderStatus:
    ok: bool
    detail: str


def _validate_gemini_sync(api_key: str) -> ProviderStatus:
    try:
        client = genai.Client(api_key=api_key)
        models = client.models.list()
        for _ in models:
            break
    except Exception as exc:
        return ProviderStatus(ok=False, detail=f"request error: {exc}")
    return ProviderStatus(ok=True, detail="listed models")


async def _validate_gemini(api_key: str) -> ProviderStatus:
    return await anyio.to_thread.run_sync(lambda: _validate_gemini_sync(api_key))


async def _validate_http(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    path: str,
    extra_headers: dict[str, str] | None = None,
) -> ProviderStatus:
    if not config.base_url:
        return ProviderStatus(ok=False, detail="missing base URL")

    url = f"{config.base_url.rstrip('/')}/{path.lstrip('/')}"
    headers, params = build_auth(config)
    if extra_headers:
        headers.update(extra_headers)

    try:
        response = await client.get(url, headers=headers or None, params=params or None)
    except httpx.HTTPError as exc:
        return ProviderStatus(ok=False, detail=f"request error: {exc}")

    if response.status_code in {401, 403}:
        return ProviderStatus(ok=False, detail=f"{response.status_code} unauthorized")
    if response.status_code >= 400:
        snippet = response.text[:300] if response.text else "<empty>"
        return ProviderStatus(ok=False, detail=f"{response.status_code} {snippet}")
    return ProviderStatus(ok=True, detail=str(response.status_code))


async def _validate_perplexity(
    client: httpx.AsyncClient,
    config: ProviderConfig,
) -> ProviderStatus:
    result = await _validate_http(client, config, "async/chat/completions")
    if result.ok or not result.detail.startswith("404 "):
        return result

    if not config.base_url:
        return result
    parsed = urlsplit(config.base_url)
    if not parsed.scheme or not parsed.netloc:
        return result

    root_base_url = f"{parsed.scheme}://{parsed.netloc}"
    if root_base_url.rstrip("/") == config.base_url.rstrip("/"):
        return result

    fallback_config = replace(config, base_url=root_base_url)
    fallback = await _validate_http(client, fallback_config, "async/chat/completions")
    if fallback.ok:
        return ProviderStatus(ok=True, detail=f"{fallback.detail} (root-host fallback)")
    return fallback


async def validate_provider(
    name: str,
    client: httpx.AsyncClient,
) -> ProviderStatus:
    config = get_provider_config(name)
    if not config.api_key:
        return ProviderStatus(ok=False, detail="no API key configured")

    if name == "gemini":
        return await _validate_gemini(config.api_key)
    if name == "perplexity":
        return await _validate_perplexity(client, config)

    path = "auth/key" if name == "openrouter" else "models"
    extra_headers = (
        {
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": config.api_key,
        }
        if name == "anthropic"
        else None
    )
    return await _validate_http(client, config, path, extra_headers)


async def validate_all_providers() -> dict[str, ProviderStatus]:
    async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT_S) as client:
        coros = {name: validate_provider(name, client) for name in PROVIDERS}
        statuses = await asyncio.gather(*coros.values())
    return dict(zip(coros.keys(), statuses))


def _cache_path() -> Path:
    raw = getenv("LLM_PROXY_VALIDATION_CACHE")
    if raw:
        return Path(raw)
    return DEFAULT_CACHE_PATH


def load_cache(path: Path) -> dict[str, ProviderStatus] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError, OSError:
        return None

    validated_at = data.get("validated_at")
    if not validated_at:
        return None

    try:
        ts = datetime.fromisoformat(validated_at)
    except ValueError:
        return None

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    if datetime.now(UTC) - ts > CACHE_MAX_AGE:
        return None

    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None

    results: dict[str, ProviderStatus] = {}
    for name, entry in providers.items():
        if isinstance(entry, dict) and "ok" in entry and "detail" in entry:
            results[name] = ProviderStatus(ok=entry["ok"], detail=entry["detail"])
    return results if results else None


def save_cache(path: Path, results: dict[str, ProviderStatus]) -> None:
    data = {
        "validated_at": datetime.now(UTC).isoformat(),
        "providers": {name: asdict(s) for name, s in results.items()},
    }
    try:
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        LOGGER.warning("Failed to write validation cache: %s", exc)


async def get_or_validate(path: Path | None = None) -> dict[str, ProviderStatus]:
    if path is None:
        path = _cache_path()

    cached = load_cache(path)
    if cached is not None:
        LOGGER.info("Using cached provider validation (from %s)", path)
        return cached

    LOGGER.info("Validating provider API keys...")
    results = await validate_all_providers()
    save_cache(path, results)
    return results
