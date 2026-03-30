import argparse
import ipaddress
import os
import re
import sys
from urllib.parse import urlsplit

import uvicorn

from llmproxy.providers import PROVIDERS

BOOL_TRUE = {"1", "true", "yes", "y", "on"}
RUN_ALT_POSTGRES_ENV = "LLM_PROXY_RUN_ALT_POSTGRES"
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9-]{1,63}$")


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _bool_env(name: str) -> bool:
    value = _env_value(name)
    if value is None:
        return False
    return value.lower() in BOOL_TRUE


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--require-db-url",
        action="store_true",
        help="Exit if LLM_PROXY_DB_URL is not set.",
    )
    return parser.parse_args(argv)


def _run_require_db_url_preflight() -> int:
    if _env_value("LLM_PROXY_DB_URL") is not None:
        return 0

    print("LLM_PROXY_DB_URL is required but not set.")
    return 1


def _missing_required_env_vars_for_alt_postgres() -> list[str]:
    required = ("LLM_PROXY_DB_URL",)
    return [name for name in required if _env_value(name) is None]


def _missing_provider_api_keys() -> list[str]:
    missing: list[str] = []
    for provider in PROVIDERS:
        if provider == "gemini":
            if _env_value("GEMINI_API_KEY") or _env_value("GOOGLE_API_KEY"):
                continue
            missing.append("GEMINI_API_KEY or GOOGLE_API_KEY")
            continue

        key = f"{provider.upper()}_API_KEY"
        if _env_value(key) is None:
            missing.append(key)

    return missing


def _is_valid_hostname(hostname: str) -> bool:
    if not hostname:
        return False

    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass

    cleaned = hostname.rstrip(".")
    if not cleaned or len(cleaned) > 253:
        return False

    labels = cleaned.split(".")
    if not labels:
        return False

    for label in labels:
        if not HOST_LABEL_RE.fullmatch(label):
            return False
        if label.startswith("-") or label.endswith("-"):
            return False

    return True


def _validate_db_url_hostname(db_url: str) -> str | None:
    hostname = urlsplit(db_url).hostname
    if not hostname:
        return "LLM_PROXY_DB_URL must include a hostname"
    if not _is_valid_hostname(hostname):
        return f"LLM_PROXY_DB_URL has an invalid hostname: {hostname}"
    return None


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_continue_for_missing_api_keys(missing_api_keys: list[str]) -> bool:
    print("Missing provider API keys:")
    for name in missing_api_keys:
        print(f"- {name}")

    while True:
        answer = input("Ignore and continue [c] or quit [q]? [q]: ").strip().lower()
        if answer in {"c", "continue", "i", "ignore"}:
            return True
        if answer in {"", "q", "quit"}:
            return False
        print("Please enter 'c' to continue or 'q' to quit.")


def _run_alt_postgres_preflight() -> int:
    missing_env = _missing_required_env_vars_for_alt_postgres()
    if missing_env:
        print("Missing required environment variables:")
        for name in missing_env:
            print(f"- {name}")
        return 1

    db_url = _env_value("LLM_PROXY_DB_URL")
    assert db_url is not None

    db_url_error = _validate_db_url_hostname(db_url)
    if db_url_error:
        print(db_url_error)
        return 1

    missing_api_keys = _missing_provider_api_keys()
    if not missing_api_keys:
        return 0

    if _is_interactive():
        if _prompt_continue_for_missing_api_keys(missing_api_keys):
            return 0
        print("Aborted.")
        return 1

    print("Missing provider API keys:")
    for name in missing_api_keys:
        print(f"- {name}")
    print(
        "Non-interactive run cannot prompt. Set the missing keys or run interactively."
    )
    return 1


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    if args.require_db_url:
        status = _run_require_db_url_preflight()
        if status:
            raise SystemExit(status)

    if _bool_env(RUN_ALT_POSTGRES_ENV):
        status = _run_alt_postgres_preflight()
        if status:
            raise SystemExit(status)

    host = os.getenv("LLM_PROXY_HOST", "0.0.0.0")
    port = int(os.getenv("LLM_PROXY_PORT", "8000"))
    reload_enabled = os.getenv("LLM_PROXY_RELOAD", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    display_host = "localhost" if host == "0.0.0.0" else host
    base = f"http://{display_host}:{port}"
    print(f"\n  Docs:   {base}/docs")
    print(f"  ReDoc:  {base}/redoc")
    print(f"  OpenAPI JSON: {base}/openapi.json\n")
    uvicorn.run("llmproxy.app:app", host=host, port=port, reload=reload_enabled)


if __name__ == "__main__":
    main()
