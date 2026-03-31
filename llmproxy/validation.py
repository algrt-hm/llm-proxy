"""validation.py: validation of LLM provider connectivity"""

import asyncio
import logging

import anyio
import httpx
from google import genai
from pydantic import BaseModel, ConfigDict
from yarl import URL

from .constants import ANTHROPIC_VERSION, PACKAGE_NAME, VALIDATION_TIMEOUT_S
from .providers import PROVIDERS, ProviderConfig, build_auth, get_provider_config

LOGGER = logging.getLogger(PACKAGE_NAME)

# Used by get_or_validate: cant't use functools.cache on an async function, so store cached results here
_cached_results: dict[str, ProviderStatus] | None = None


class ProviderStatus(BaseModel):
    """Small class to hold status and any message resulting from validation"""

    model_config = ConfigDict(frozen=True)

    ok: bool
    detail: str


def _validate_gemini_sync(api_key: str) -> ProviderStatus:
    """Check we can list models with the Gemini SDK, to test API key works etc.

    Args:
        api_key (str): API key

    Returns:
        ProviderStatus: instantiation
    """

    try:
        # Create client
        client = genai.Client(api_key=api_key)

        # Get list of models
        models = client.models.list()

        # Iterate to ensure we can access the models
        for _ in models:
            # We don't actually care about the models here, just that we can iterate without an error
            break

    except Exception as exception:
        # Catch all exceptions since the Gemini SDK could raise various types of errors
        return ProviderStatus(ok=False, detail=f"request error: {exception}")

    # Otherwise, all good
    return ProviderStatus(ok=True, detail="listed models")


async def _validate_gemini(api_key: str) -> ProviderStatus:
    """Check Gemini API working. Note that the Gemini SDK is synchronous, so run in a thread to avoid blocking the event loop

    Args:
        api_key (str): API key

    Returns:
        ProviderStatus: instantiation
    """

    return await anyio.to_thread.run_sync(lambda: _validate_gemini_sync(api_key))


async def _validate_http(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    path: str,
    extra_headers: dict[str, str] | None = None,
) -> ProviderStatus:
    """Used for both OpenRouter and Anthropic, which have similar auth and model listing endpoints

    Args:
        client (httpx.AsyncClient): HTTP client to use for request
        config (ProviderConfig): config for LLM provider
        path (str): API path, concatenated with base_url to form full URL for request
        extra_headers (dict[str, str] | None, optional): Additional headers to include in the request. Defaults to None.

    Returns:
        ProviderStatus: instantiation
    """

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


async def _validate_perplexity(client: httpx.AsyncClient, config: ProviderConfig) -> ProviderStatus:
    """Check Perplexity API working. Note that Perplexity can sometimes give a 404 if the base URL includes a path (e.g. from an old config) even if the root URL is correct, so if we get a 404 we also try again with just the root URL as a fallback

    Args:
        client (httpx.AsyncClient): HTTP client to use for request
        config (ProviderConfig): config for LLM provider

    Returns:
        ProviderStatus: instantiation
    """

    # OK or no 404, good
    result = await _validate_http(client, config, "async/chat/completions")
    if result.ok or not result.detail.startswith("404 "):
        return result

    # No base url?
    if not config.base_url:
        # We don't e.g.
        # raise ValueError("missing base URL")
        # because we don't want to trash the async loop
        return result

    url = URL(config.base_url)

    # Attempt to check base_url
    if not url.scheme or not url.host:
        return result

    # This checks whether the root origin is the same as the already-configured base URL (ignoring trailing slashes)
    # If they're equal, there's no point retrying — the fallback URL would be identical to what already failed — so return 404
    root_base_url = url.origin().human_repr()
    if root_base_url.rstrip("/") == config.base_url.rstrip("/"):
        return result

    # Try again with root URL as a fallback, in case the 404 is due to an extra path in the base URL
    fallback_config = config.model_copy(update={"base_url": root_base_url})
    fallback = await _validate_http(client, fallback_config, "async/chat/completions")

    # All good
    if fallback.ok:
        return ProviderStatus(ok=True, detail=f"{fallback.detail} (root-host fallback)")

    # Return status from fallback config in any event
    return fallback


async def validate_provider(
    provider_name: str,
    client: httpx.AsyncClient,
) -> ProviderStatus:
    """Call correct validation function for the given provider

    Args:
        provider_name (str): provider name
        client (httpx.AsyncClient): HTTP client to use for request

    Returns:
        ProviderStatus: instantiation
    """

    config = get_provider_config(provider_name)

    if not config.api_key:
        return ProviderStatus(ok=False, detail="no API key configured")

    # Gemini
    if provider_name == "gemini":
        return await _validate_gemini(config.api_key)

    # Perplexity
    if provider_name == "perplexity":
        return await _validate_perplexity(client, config)

    # Below used for both OpenRouter and Anthropic, which have similar auth and model listing endpoints
    path = "auth/key" if provider_name == "openrouter" else "models"

    # Extra headers needed for Anthropic, which requires both the version and API key
    extra_headers = (
        {
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": config.api_key,
        }
        if provider_name == "anthropic"
        else None
    )

    # OpenRouter and Anthropic
    return await _validate_http(client, config, path, extra_headers)


async def validate_all_providers() -> dict[str, ProviderStatus]:
    """Validate all providers concurrently

    Returns:
        dict[str, ProviderStatus]: a dict of provider name to status
    """

    # This allows connection pooling w/ timeout for async requests
    async with httpx.AsyncClient(timeout=VALIDATION_TIMEOUT_S) as client:
        # The validations to run concurrently, as a dict of provider name to coroutine
        coros = {name: validate_provider(name, client) for name in PROVIDERS}

        # Run and gather results concurrently; splat expands to arguments
        statuses = await asyncio.gather(*coros.values())

    # Return dict of provider name and status
    return dict(zip(coros.keys(), statuses))


async def get_or_validate() -> dict[str, ProviderStatus]:
    """Return cache or validate all providers concurrently

    Returns:
        dict[str, ProviderStatus]: a dict of provider name to status
    """
    global _cached_results

    if _cached_results is not None:
        return _cached_results

    LOGGER.info("Validating provider API keys...")
    _cached_results = await validate_all_providers()
    return _cached_results
