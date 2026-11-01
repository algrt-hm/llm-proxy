import pytest
import uvicorn.config

import main
from llmproxy.constants import PACKAGE_NAME


def test_parse_args_defaults():
    args = main._parse_args([])
    assert args.require_db_url is False


def test_parse_args_require_db_url_enabled():
    args = main._parse_args(["--require-db-url"])
    assert args.require_db_url is True


def test_require_db_url_preflight_accepts_present_env(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_DB_URL", "sqlite+aiosqlite:///./llmproxy.db")
    assert main._run_require_db_url_preflight() == 0


def test_require_db_url_preflight_rejects_missing_env(monkeypatch, capsys):
    monkeypatch.delenv("LLM_PROXY_DB_URL", raising=False)
    assert main._run_require_db_url_preflight() == 1
    captured = capsys.readouterr()
    assert "LLM_PROXY_DB_URL is required but not set." in captured.out


def test_validate_db_url_hostname_accepts_localhost():
    assert main._validate_db_url_hostname("postgresql+asyncpg://postgres@localhost/llm-proxy") is None


def test_validate_db_url_hostname_requires_hostname():
    assert main._validate_db_url_hostname("postgresql+asyncpg://postgres@/llm-proxy") == ("LLM_PROXY_DB_URL must include a hostname")


def test_validate_db_url_hostname_rejects_invalid_hostname():
    assert main._validate_db_url_hostname("postgresql+asyncpg://postgres@bad_host/llm-proxy") == (
        "LLM_PROXY_DB_URL has an invalid hostname: bad_host"
    )


def test_missing_provider_api_keys_uses_google_api_key_for_gemini(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "x")
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("CEREBRAS_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    assert main._missing_provider_api_keys() == []


def test_log_level_defaults_to_info(monkeypatch):
    monkeypatch.delenv("LLM_PROXY_LOG_LEVEL", raising=False)
    assert main._log_level() == "INFO"


def test_log_level_from_env_is_upper_cased(monkeypatch):
    monkeypatch.setenv("LLM_PROXY_LOG_LEVEL", " debug ")
    assert main._log_level() == "DEBUG"


def test_log_level_rejects_unknown_name(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROXY_LOG_LEVEL", "verbose")
    with pytest.raises(SystemExit) as excinfo:
        main._log_level()
    assert excinfo.value.code == 1
    assert "LLM_PROXY_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL" in capsys.readouterr().out


def test_log_config_gives_the_package_logger_uvicorns_handler():
    """The proxy's own INFO lines reach stderr, timestamped like uvicorn's,
    instead of being dropped at the root logger's WARNING default."""
    config = main._log_config("DEBUG")
    assert config["loggers"][PACKAGE_NAME] == {"handlers": ["default"], "level": "DEBUG", "propagate": False}
    assert config["loggers"]["uvicorn"]["handlers"] == ["default"]
    assert config["formatters"]["default"]["fmt"].startswith("%(asctime)s")
    # uvicorn's own table is copied, never edited in place.
    assert PACKAGE_NAME not in uvicorn.config.LOGGING_CONFIG["loggers"]
