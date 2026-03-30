#!/usr/bin/env python3
import json
import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

PROVIDERS = (
    "openai",
    "openrouter",
    "perplexity",
    "gemini",
    "cerebras",
    "anthropic",
)
BOOL_TRUE = {"1", "true", "yes", "y", "on"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_BASE_URL = "http://localhost:6969"


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in BOOL_TRUE


def _provider_api_key_envs(name: str) -> tuple[str, ...]:
    if name == "gemini":
        return ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    return (f"{name.upper()}_API_KEY",)


def _redact_key(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _provider_api_keys_debug(name: str) -> str:
    parts = []
    for env_name in _provider_api_key_envs(name):
        value = os.getenv(env_name)
        parts.append(f"{env_name}={_redact_key(value)}")
    return ", ".join(parts)


def _providers_to_test() -> list[str]:
    raw = os.getenv("LLM_PROXY_TEST_PROVIDERS", "").strip()
    if not raw:
        return list(PROVIDERS)
    providers = []
    for item in raw.split(","):
        name = item.strip().lower()
        if name:
            providers.append(name)
    return providers


def _should_test_provider(name: str) -> bool:
    if _bool_env("LLM_PROXY_TEST_FORCE_ALL"):
        return True
    return any(os.getenv(env_name) for env_name in _provider_api_key_envs(name))


def _model_for_provider(name: str) -> str | None:
    return os.getenv(f"LLM_PROXY_TEST_MODEL_{name.upper()}")


def _is_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and "error" in payload


def _normalize_local_base_url(value: str) -> tuple[str, str | None]:
    raw = value.strip()
    if not raw:
        raw = DEFAULT_BASE_URL
    if "://" not in raw:
        raw = f"http://{raw}"
    raw = raw.rstrip("/")

    parsed = urlsplit(raw)
    host = parsed.hostname
    if not host or host not in LOCAL_HOSTS or parsed.port == 6969:
        return raw, None

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        userinfo = f"{userinfo}@"

    host_part = host
    if ":" in host_part and not host_part.startswith("["):
        host_part = f"[{host_part}]"

    corrected_netloc = f"{userinfo}{host_part}:6969"
    corrected = urlunsplit(
        (
            parsed.scheme or "http",
            corrected_netloc,
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    ).rstrip("/")
    return corrected, raw


def main() -> int:
    base_url, corrected_from = _normalize_local_base_url(
        os.getenv("LLM_PROXY_BASE_URL", DEFAULT_BASE_URL)
    )
    timeout_s = float(os.getenv("LLM_PROXY_TEST_TIMEOUT_S", "60"))
    endpoint = f"{base_url}/v1/chat/completions"

    providers = _providers_to_test()
    failures = 0
    skipped = 0

    with httpx.Client(timeout=timeout_s) as client:
        if corrected_from:
            print(
                f"[INFO] corrected LLM_PROXY_BASE_URL port to 6969: "
                f"{corrected_from} -> {base_url}"
            )

        for provider in providers:
            if provider not in PROVIDERS:
                print(f"[FAIL] {provider}: unknown provider name")
                failures += 1
                continue

            if not _should_test_provider(provider):
                print(
                    f"[SKIP] {provider}: missing {provider.upper()}_API_KEY locally "
                    "(set LLM_PROXY_TEST_FORCE_ALL=1 to test anyway)"
                )
                skipped += 1
                continue

            model = _model_for_provider(provider)
            if not model:
                print(
                    f"[SKIP] {provider}: missing LLM_PROXY_TEST_MODEL_{provider.upper()} "
                    "for a provider-specific model name"
                )
                skipped += 1
                continue

            payload = {
                "model": f"{provider}:{model}",
                "messages": [{"role": "user", "content": "ping"}],
            }

            try:
                response = client.post(endpoint, json=payload)
            except httpx.HTTPError as exc:
                print(
                    f"[FAIL] {provider}: request error: {exc} "
                    f"({_provider_api_keys_debug(provider)})"
                )
                failures += 1
                continue

            response_text = response.text
            response_payload: Any = None
            if response_text:
                try:
                    response_payload = response.json()
                except json.JSONDecodeError:
                    response_payload = None

            if response.status_code >= 400 or _is_error_payload(response_payload):
                snippet = response_text[:500] if response_text else "<empty>"
                print(
                    f"[FAIL] {provider}: {response.status_code} {snippet} "
                    f"({_provider_api_keys_debug(provider)})"
                )
                failures += 1
                continue

            print(f"[OK] {provider}: {response.status_code}")

    print(
        f"\nSummary: {len(providers) - skipped - failures} ok, {skipped} skipped, {failures} failed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
