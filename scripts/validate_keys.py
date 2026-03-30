#!/usr/bin/env python3
import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from llmproxy.providers import PROVIDERS, get_provider_config  # noqa: E402
from llmproxy.validation import (  # noqa: E402
    VALIDATION_TIMEOUT_S,
    validate_provider,
)

BOOL_TRUE = {"1", "true", "yes", "y", "on"}


def _bool_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in BOOL_TRUE


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
    return bool(os.getenv(f"{name.upper()}_API_KEY"))


async def _run() -> int:
    providers = _providers_to_test()
    failures = 0
    skipped = 0

    timeout_s = float(os.getenv("LLM_PROXY_TEST_TIMEOUT_S", str(VALIDATION_TIMEOUT_S)))
    async with httpx.AsyncClient(timeout=timeout_s) as client:
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

            config = get_provider_config(provider)
            if not config.api_key:
                print(
                    f"[SKIP] {provider}: no configured API key after provider config lookup"
                )
                skipped += 1
                continue

            result = await validate_provider(provider, client)

            if result.ok:
                print(f"[OK] {provider}: {result.detail}")
            else:
                print(f"[FAIL] {provider}: {result.detail}")
                failures += 1

    print(
        f"\nSummary: {len(providers) - skipped - failures} ok, {skipped} skipped, {failures} failed"
    )
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
