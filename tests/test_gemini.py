from llmproxy.gemini import build_gemini_config


def test_no_reasoning_leaves_thinking_unset():
    """With no reasoning param, thinking_config is not set (model default)."""
    config = build_gemini_config({"max_tokens": 200}, system_instruction=None)
    assert config is not None
    assert config.thinking_config is None


def test_thinking_disabled_when_reasoning_false():
    """Explicit reasoning:{enabled:false} disables thinking."""
    payload = {"max_tokens": 200, "reasoning": {"enabled": False}}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


def test_thinking_enabled_with_budget():
    """reasoning:{enabled:true, budget:500} passes through thinking_budget."""
    payload = {"max_tokens": 1000, "reasoning": {"enabled": True, "budget": 500}}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 500


def test_thinking_enabled_without_budget():
    """reasoning:{enabled:true} with no budget omits thinking_config (Gemini default)."""
    payload = {"max_tokens": 1000, "reasoning": {"enabled": True}}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.thinking_config is None


def test_max_tokens_mapped():
    """max_tokens is mapped to max_output_tokens."""
    config = build_gemini_config({"max_tokens": 42}, system_instruction=None)
    assert config is not None
    assert config.max_output_tokens == 42


def test_system_instruction_passed():
    config = build_gemini_config({}, system_instruction="Be helpful")
    assert config is not None
    assert config.system_instruction == "Be helpful"


def test_empty_payload_returns_none():
    """No system instruction, no params → None config."""
    config = build_gemini_config({}, system_instruction=None)
    assert config is None


def test_temperature_and_top_p():
    payload = {"temperature": 0.7, "top_p": 0.9}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.temperature == 0.7
    assert config.top_p == 0.9


def test_stop_string():
    payload = {"stop": "END"}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.stop_sequences == ["END"]


def test_stop_list():
    payload = {"stop": ["END", "STOP"]}
    config = build_gemini_config(payload, system_instruction=None)
    assert config is not None
    assert config.stop_sequences == ["END", "STOP"]
