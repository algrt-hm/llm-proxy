import main


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
