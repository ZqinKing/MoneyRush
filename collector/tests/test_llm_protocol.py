from __future__ import annotations

from types import SimpleNamespace

from shared.llm_protocol import (
    ANTHROPIC_MESSAGES_PROTOCOL,
    OPENAI_CHAT_PROTOCOL,
    OPENAI_RESPONSES_PROTOCOL,
    build_llm_request,
    parse_llm_response,
)


def _settings(**overrides):
    defaults = {
        "ai_base_url": "https://llm.example/v1",
        "ai_api_key": "secret",
        "ai_provider": "openai",
        "ai_protocol": OPENAI_CHAT_PROTOCOL,
        "ai_model": "test-model",
        "ai_fallback_model": None,
        "ai_thinking_enabled": False,
        "ai_reasoning_enabled": False,
        "ai_reasoning_effort": "low",
        "ai_reasoning_budget_tokens": 2048,
        "ai_max_output_tokens": 8192,
        "ai_max_tokens": 8192,
        "ai_anthropic_version": "2023-06-01",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_openai_chat_payload_uses_standard_fields_without_reasoning_by_default():
    request = build_llm_request(_settings(), model="gpt-test", system_prompt="sys", user_prompt="user", max_tokens=123)

    assert request.protocol == OPENAI_CHAT_PROTOCOL
    assert request.url == "https://llm.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.payload["model"] == "gpt-test"
    assert request.payload["max_tokens"] == 123
    assert "reasoning_split" not in request.payload
    assert "context_length" not in request.payload
    assert "thinking" not in request.payload


def test_openai_chat_reasoning_uses_reasoning_effort_only():
    request = build_llm_request(
        _settings(ai_reasoning_enabled=True, ai_reasoning_effort="medium"),
        model="gpt-test",
        system_prompt="sys",
        user_prompt="user",
    )

    assert request.payload["reasoning_effort"] == "medium"
    assert "thinking" not in request.payload


def test_openai_responses_payload_and_response_parsing():
    request = build_llm_request(
        _settings(ai_protocol=OPENAI_RESPONSES_PROTOCOL, ai_reasoning_enabled=True),
        model="gpt-test",
        system_prompt="sys",
        user_prompt="user",
        max_tokens=321,
    )

    assert request.url == "https://llm.example/v1/responses"
    assert request.payload["instructions"] == "sys"
    assert request.payload["input"] == "user"
    assert request.payload["max_output_tokens"] == 321
    assert request.payload["reasoning"] == {"effort": "low"}

    parsed = parse_llm_response({"status": "completed", "output_text": "final", "usage": {"total_tokens": 7}}, protocol=OPENAI_RESPONSES_PROTOCOL)

    assert parsed.text == "final"
    assert parsed.finish_reason == "completed"
    assert parsed.usage == {"total_tokens": 7}


def test_anthropic_messages_payload_and_response_parsing():
    request = build_llm_request(
        _settings(ai_provider="anthropic", ai_protocol=ANTHROPIC_MESSAGES_PROTOCOL, ai_reasoning_enabled=True, ai_reasoning_budget_tokens=2048),
        model="claude-test",
        system_prompt="sys",
        user_prompt="user",
        max_tokens=4096,
    )

    assert request.url == "https://llm.example/v1/messages"
    assert request.headers["x-api-key"] == "secret"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.payload["system"] == "sys"
    assert request.payload["messages"] == [{"role": "user", "content": "user"}]
    assert request.payload["thinking"] == {"type": "enabled", "budget_tokens": 2048}

    parsed = parse_llm_response(
        {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "hidden reasoning"},
                {"type": "text", "text": "visible answer"},
            ],
            "usage": {"input_tokens": 3, "output_tokens": 5},
        },
        protocol=ANTHROPIC_MESSAGES_PROTOCOL,
    )

    assert parsed.text == "visible answer"
    assert parsed.finish_reason == "end_turn"
    assert parsed.reasoning_content_length == len("hidden reasoning")


def test_legacy_ai_thinking_enabled_enables_protocol_reasoning():
    openai_request = build_llm_request(_settings(ai_thinking_enabled=True), model="gpt-test", system_prompt="sys", user_prompt="user")
    anthropic_request = build_llm_request(
        _settings(ai_protocol=ANTHROPIC_MESSAGES_PROTOCOL, ai_thinking_enabled=True),
        model="claude-test",
        system_prompt="sys",
        user_prompt="user",
    )

    assert openai_request.payload["reasoning_effort"] == "low"
    thinking = anthropic_request.payload["thinking"]
    assert isinstance(thinking, dict)
    assert thinking["type"] == "enabled"
