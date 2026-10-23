"""Offline tests for the KUREK_LLM=haiku translation layer."""
from core import llm_client


def test_history_translates_to_messages_api() -> None:
    system, msgs = llm_client._to_anthropic_messages([
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "weather_report", "arguments": '{"city": "Ljubljana"}'}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "12C"},
        {"role": "user", "content": "say it short"},
    ])
    assert system == "be terse"
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == [{"type": "tool_use", "id": "c1", "name": "weather_report", "input": {"city": "Ljubljana"}}]
    # tool result first, then the follow-up text, in one user turn
    assert msgs[2]["content"][0] == {"type": "tool_result", "tool_use_id": "c1", "content": "12C"}
    assert msgs[2]["content"][1] == {"type": "text", "text": "say it short"}


def test_raw_haiku_blocks_are_sent_back_unchanged() -> None:
    raw = [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
    _, msgs = llm_client._to_anthropic_messages([
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [], "reasoning_content": raw},
    ])
    assert msgs[1]["content"] == raw


def test_tools_translate_and_default_schema() -> None:
    tools = llm_client._to_anthropic_tools([
        {"type": "function", "function": {"name": "a", "description": "d", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}},
        {"type": "function", "function": {"name": "b", "parameters": {}}},
    ])
    assert tools[0] == {"name": "a", "description": "d", "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}}
    assert tools[1]["input_schema"] == {"type": "object", "properties": {}}


def test_switch_routes_stream_and_needs_no_deepseek_key(monkeypatch) -> None:
    from memory import config_manager

    monkeypatch.setenv("KUREK_LLM", "haiku")
    monkeypatch.setattr(config_manager, "get_deepseek_key", lambda: None)
    monkeypatch.setattr(config_manager, "get_anthropic_key", lambda: None)
    events = list(llm_client.stream_deepseek(messages=[{"role": "user", "content": "hi"}]))
    assert events == [{"type": "error", "error": "No ANTHROPIC_API_KEY configured"}]
    assert llm_client.brain_name() == "Claude Haiku"
