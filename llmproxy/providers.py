from dataclasses import dataclass
from os import getenv

PROVIDERS = (
    "openai",
    "openrouter",
    "perplexity",
    "gemini",
    "cerebras",
    "anthropic",
)
DEFAULT_PROVIDER = getenv("LLM_PROXY_DEFAULT_PROVIDER", "openai").strip().lower()
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "perplexity": "https://api.perplexity.ai/v2",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "cerebras": "https://api.cerebras.ai/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key: str | None
    base_url: str | None
    auth_header: str | None
    auth_prefix: str | None
    auth_query_param: str | None


def parse_model(model: str) -> tuple[str, str]:
    cleaned = model.strip()
    if ":" in cleaned:
        prefix, rest = cleaned.split(":", 1)
        prefix = prefix.lower()
        if prefix in PROVIDERS and rest:
            return prefix, rest
    if "/" in cleaned:
        prefix, rest = cleaned.split("/", 1)
        prefix = prefix.lower()
        if prefix in PROVIDERS and rest:
            return prefix, rest
    return DEFAULT_PROVIDER, cleaned


def get_provider_config(name: str) -> ProviderConfig:
    key = name.upper()
    if name == "gemini":
        api_key = getenv("GEMINI_API_KEY") or getenv("GOOGLE_API_KEY")
    else:
        api_key = getenv(f"{key}_API_KEY")
    base_url = getenv(f"{key}_BASE_URL") or DEFAULT_BASE_URLS.get(name)
    auth_header = getenv(f"{key}_AUTH_HEADER", "Authorization")
    auth_prefix = getenv(f"{key}_AUTH_PREFIX", "Bearer")
    auth_query_param = getenv(f"{key}_AUTH_QUERY")
    return ProviderConfig(
        name=name,
        api_key=api_key,
        base_url=base_url,
        auth_header=auth_header,
        auth_prefix=auth_prefix,
        auth_query_param=auth_query_param,
    )


def build_auth(config: ProviderConfig) -> tuple[dict[str, str], dict[str, str]]:
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if not config.api_key:
        return headers, params

    if config.auth_query_param:
        params[config.auth_query_param] = config.api_key
    elif config.auth_header:
        if config.auth_prefix:
            headers[config.auth_header] = f"{config.auth_prefix} {config.api_key}"
        else:
            headers[config.auth_header] = config.api_key

    return headers, params


def build_chat_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/chat/completions"):
        return trimmed
    return f"{trimmed}/chat/completions"


def build_embeddings_url(base_url: str) -> str:
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/embeddings"):
        return trimmed
    if trimmed.endswith("/chat/completions"):
        trimmed = trimmed[: -len("/chat/completions")]
    return f"{trimmed}/embeddings"
