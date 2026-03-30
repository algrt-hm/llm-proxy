import asyncio
import logging

import anyio
import httpx
from google import genai
from pydantic import BaseModel, ConfigDict
from yarl import URL

from .providers import PROVIDERS, ProviderConfig, build_auth, get_provider_config

LOGGER = logging.getLogger("llmproxy")

# TODO: understand this relates to the API version
ANTHROPIC_VERSION = "2023-06-01"
VALIDATION_TIMEOUT_S = 30.0


class ProviderStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    detail: str


def _validate_gemini_sync(api_key: str) -> ProviderStatus:
    try:
        client = genai.Client(api_key=api_key)
        models = client.models.list()
        for _ in models:
            break
    except Exception as exception:
        return ProviderStatus(ok=False, detail=f"request error: {exception}")
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
    except httpx.HTTPError as exception:
        return ProviderStatus(ok=False, detail=f"request error: {exception}")

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
    parsed = URL(config.base_url)
    if not parsed.scheme or not parsed.host:
        return result

    root_base_url = parsed.origin().human_repr()
    if root_base_url.rstrip("/") == config.base_url.rstrip("/"):
        return result

    fallback_config = config.model_copy(update={"base_url": root_base_url})
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


_cached_results: dict[str, ProviderStatus] | None = None


async def validate_all_providers() -> dict[str, ProviderStatus]:
    async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT_S) as client:
        coros = {name: validate_provider(name, client) for name in PROVIDERS}
        statuses = await asyncio.gather(*coros.values())
    return dict(zip(coros.keys(), statuses))


async def get_or_validate() -> dict[str, ProviderStatus]:
    # We can't use functools.cache on an async function hence this
    global _cached_results
    if _cached_results is not None:
        return _cached_results

    LOGGER.info("Validating provider API keys...")
    _cached_results = await validate_all_providers()
    return _cached_results
