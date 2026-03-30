import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from os import getenv
from pathlib import Path
from typing import Any

import anyio
import httpx
from google import genai

from .providers import PROVIDERS, ProviderConfig, build_auth, get_provider_config

LOGGER = logging.getLogger("llmproxy")
ANTHROPIC_VERSION = "2023-06-01"
CACHE_MAX_AGE = timedelta(hours=24)
DEFAULT_CACHE_PATH = Path("./llmproxy_models.json")
FETCH_TIMEOUT_S = 30.0
MODELS_DEV_URL = "https://models.dev/api.json"

# type alias for a single provider's model list
ModelList = list[dict[str, Any]]

# maps llmproxy provider names to models.dev top-level keys
_MODELS_DEV_PROVIDERS: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "cerebras": "cerebras",
    "perplexity": "perplexity",
}

PERPLEXITY_MODELS: ModelList = [
    {"id": "sonar", "object": "model"},
    {"id": "sonar-pro", "object": "model"},
    {"id": "sonar-reasoning-pro", "object": "model"},
]


def _unix_to_iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _model_entry(id: str, **extra: Any) -> dict[str, Any]:
    """Build a normalised model dict, dropping None values."""
    entry: dict[str, Any] = {"id": id, "object": "model"}
    for key, val in extra.items():
        if val is not None:
            entry[key] = val
    return entry


def _list_gemini_models_sync(api_key: str) -> ModelList:
    client = genai.Client(api_key=api_key)
    models: ModelList = []
    for m in client.models.list():
        models.append(
            _model_entry(
                m.name,
                display_name=getattr(m, "display_name", None),
                description=getattr(m, "description", None),
                input_token_limit=getattr(m, "input_token_limit", None),
                output_token_limit=getattr(m, "output_token_limit", None),
            )
        )
    return sorted(models, key=lambda m: m["id"])


async def _fetch_gemini(api_key: str) -> ModelList:
    return await anyio.to_thread.run_sync(lambda: _list_gemini_models_sync(api_key))


def _parse_openrouter_model(item: dict[str, Any]) -> dict[str, Any]:
    pricing = item.get("pricing") or {}
    arch = item.get("architecture") or {}
    return _model_entry(
        item["id"],
        name=item.get("name"),
        created=_unix_to_iso(item.get("created")),
        description=item.get("description"),
        context_length=item.get("context_length"),
        pricing_prompt=pricing.get("prompt"),
        pricing_completion=pricing.get("completion"),
        modality=arch.get("modality"),
    )


def _parse_openai_model(item: dict[str, Any]) -> dict[str, Any]:
    return _model_entry(
        item["id"],
        created=_unix_to_iso(item.get("created")),
        owned_by=item.get("owned_by"),
    )


async def _fetch_openai_format(
    client: httpx.AsyncClient,
    config: ProviderConfig,
    *,
    parser: Any = _parse_openai_model,
    extra_headers: dict[str, str] | None = None,
) -> ModelList:
    url = f"{config.base_url.rstrip('/')}/models"
    headers, params = build_auth(config)
    if extra_headers:
        headers.update(extra_headers)

    response = await client.get(url, headers=headers or None, params=params or None)
    response.raise_for_status()
    data = response.json()
    models = [parser(item) for item in data.get("data", [])]
    return sorted(models, key=lambda m: m["id"])


async def _fetch_anthropic(
    client: httpx.AsyncClient,
    config: ProviderConfig,
) -> ModelList:
    url = f"{config.base_url.rstrip('/')}/models"
    headers, params = build_auth(config)
    headers["anthropic-version"] = ANTHROPIC_VERSION
    headers["x-api-key"] = config.api_key

    models: ModelList = []
    after_id: str | None = None

    while True:
        req_params = dict(params)
        if after_id:
            req_params["after_id"] = after_id

        response = await client.get(
            url, headers=headers or None, params=req_params or None
        )
        response.raise_for_status()
        body = response.json()
        for item in body.get("data", []):
            models.append(
                _model_entry(
                    item["id"],
                    display_name=item.get("display_name"),
                    created=item.get("created_at"),
                )
            )

        if body.get("has_more") and body.get("last_id"):
            after_id = body["last_id"]
        else:
            break

    return sorted(models, key=lambda m: m["id"])


async def fetch_models(name: str, client: httpx.AsyncClient) -> ModelList:
    config = get_provider_config(name)
    if not config.api_key:
        return []

    if name == "gemini":
        return await _fetch_gemini(config.api_key)

    if name == "perplexity":
        return list(PERPLEXITY_MODELS)

    if not config.base_url:
        return []

    if name == "anthropic":
        return await _fetch_anthropic(client, config)

    if name == "openrouter":
        return await _fetch_openai_format(
            client, config, parser=_parse_openrouter_model
        )

    # openai, cerebras — standard OpenAI format
    return await _fetch_openai_format(client, config)


async def _fetch_models_dev(client: httpx.AsyncClient) -> dict:
    """Fetch the models.dev registry. Returns {} on failure."""
    try:
        resp = await client.get(MODELS_DEV_URL)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to fetch models.dev: %s", exc)
        return {}


def _build_context_lookup(
    models_dev: dict,
) -> dict[str, dict[str, dict[str, int]]]:
    """Build {llmproxy_provider: {model_id: {"context": N, "output": N}}}."""
    lookup: dict[str, dict[str, dict[str, int]]] = {}
    for local_name, dev_key in _MODELS_DEV_PROVIDERS.items():
        provider_data = models_dev.get(dev_key)
        if not provider_data:
            continue
        provider_models = provider_data.get("models")
        if not isinstance(provider_models, dict):
            continue
        entries: dict[str, dict[str, int]] = {}
        for mid, m in provider_models.items():
            limits = m.get("limit") or {} if isinstance(m, dict) else {}
            info: dict[str, int] = {}
            if isinstance(limits.get("context"), int):
                info["context"] = limits["context"]
            if isinstance(limits.get("output"), int):
                info["output"] = limits["output"]
            if info:
                entries[mid] = info
        if entries:
            lookup[local_name] = entries
    return lookup


def _enrich_models(
    results: dict[str, ModelList],
    lookup: dict[str, dict[str, dict[str, int]]],
) -> None:
    """Add context_length / max_output_tokens to model entries in-place."""
    for provider, models in results.items():
        provider_lookup = lookup.get(provider, {})
        for entry in models:
            # OpenRouter already provides context_length — skip
            if "context_length" in entry:
                continue

            # Gemini: use SDK-provided input_token_limit if available
            if provider == "gemini" and "input_token_limit" in entry:
                entry["context_length"] = entry["input_token_limit"]
                continue

            # look up in models.dev data
            model_id = entry.get("id", "")
            # Gemini model IDs are prefixed with "models/"
            if provider == "gemini":
                model_id = model_id.removeprefix("models/")
            info = provider_lookup.get(model_id)
            if not info:
                continue
            if "context" in info:
                entry["context_length"] = info["context"]
            if "output" in info and "max_output_tokens" not in entry:
                entry["max_output_tokens"] = info["output"]


async def fetch_all_models() -> dict[str, ModelList]:
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as client:
        # Determine which providers have keys configured.
        names: list[str] = []
        for name in PROVIDERS:
            config = get_provider_config(name)
            if not config.api_key:
                LOGGER.info("Skipping model fetch for %s (no API key)", name)
                continue
            names.append(name)

        async def _safe_fetch(name: str) -> tuple[str, ModelList]:
            try:
                models = await fetch_models(name, client)
                LOGGER.info("Fetched %d models for %s", len(models), name)
                return name, models
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to fetch models for %s: %s", name, exc)
                return name, []

        # Fetch all providers + models.dev concurrently.
        provider_tasks = [_safe_fetch(n) for n in names]
        dev_task = _fetch_models_dev(client)
        all_results = await asyncio.gather(*provider_tasks, dev_task)

        models_dev = all_results[-1]
        results: dict[str, ModelList] = dict(all_results[:-1])
        lookup = _build_context_lookup(models_dev)
        _enrich_models(results, lookup)
    return results


def _cache_path() -> Path:
    raw = getenv("LLM_PROXY_MODELS_CACHE")
    if raw:
        return Path(raw)
    return DEFAULT_CACHE_PATH


def load_model_cache(path: Path) -> dict[str, ModelList] | None:
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

    providers = data.get("providers")
    if not isinstance(providers, dict):
        return None

    results: dict[str, ModelList] = {}
    for name, models in providers.items():
        if isinstance(models, list):
            results[name] = models
    return results if results else None


def save_model_cache(path: Path, results: dict[str, ModelList]) -> None:
    data = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "providers": results,
    }
    try:
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError as exc:
        LOGGER.warning("Failed to write model cache: %s", exc)


async def get_or_fetch_models(path: Path | None = None) -> dict[str, ModelList]:
    if path is None:
        path = _cache_path()

    cached = load_model_cache(path)
    if cached is not None:
        LOGGER.info("Using cached model list (from %s)", path)
        return cached

    LOGGER.info("Fetching provider model lists...")
    results = await fetch_all_models()
    save_model_cache(path, results)
    return results
