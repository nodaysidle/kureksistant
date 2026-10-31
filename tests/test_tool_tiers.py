"""Rarely used tool schemas stay out of the prompt until the model loads them."""
from __future__ import annotations

from pathlib import Path

import kurek_daemon as daemon
from core import tool_tiers
from core.action_loader import discover_actions
from core.live_voice import READ_ONLY_TOOLS
from tests.test_stream_errors import _make_lite_engine

ROOT = Path(__file__).resolve().parents[1]


def _tool(name: str, desc: str = "Does a thing. More detail.") -> dict:
    return {"type": "function", "function": {
        "name": name, "description": desc, "parameters": {"type": "object", "properties": {}}}}


def _names(tools) -> list[str]:
    return [t["function"]["name"] for t in tools or []]


def test_every_deferred_name_is_a_real_action():
    names = discover_actions(ROOT / "actions", logger=lambda _m: None).names()
    assert tool_tiers.DEFERRED <= names


def test_live_voice_keeps_its_tools():
    assert not set(READ_ONLY_TOOLS) & tool_tiers.DEFERRED


def test_split_preserves_order():
    always, deferred = tool_tiers.split([_tool("web_search"), _tool("flight_finder"), _tool("run")])
    assert _names(always) == ["web_search", "run"]
    assert list(deferred) == ["flight_finder"]


def test_loader_lists_deferred_tools_sorted():
    _, deferred = tool_tiers.split([_tool("youtube_video", "Plays videos. Long."), _tool("flight_finder")])
    loader = tool_tiers.loader_tool(deferred)["function"]
    assert loader["name"] == tool_tiers.LOADER
    assert loader["parameters"]["properties"]["names"]["items"]["enum"] == ["flight_finder", "youtube_video"]
    assert "youtube_video: Plays videos." in loader["description"]
    assert "Long." not in loader["description"]


def test_load_appends_once_and_reports_unknown():
    _, deferred = tool_tiers.split([_tool("flight_finder")])
    turn = [_tool("web_search")]
    assert "flight_finder" in tool_tiers.load(["flight_finder"], deferred, turn)
    tool_tiers.load(["flight_finder"], deferred, turn)
    assert _names(turn) == ["web_search", "flight_finder"]
    assert "nope" in tool_tiers.load(["nope"], deferred, turn)
    assert _names(turn) == ["web_search", "flight_finder"]


def test_loaded_tool_is_sent_for_the_rest_of_the_turn_only(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    engine.openai_tools, engine.deferred_tools = tool_tiers.split(
        [_tool("web_search"), _tool("flight_finder")])
    engine.openai_tools.append(tool_tiers.loader_tool(engine.deferred_tools))

    sent: list[list[str]] = []
    personas: list[str] = []
    script = iter([
        [{"type": "tool_calls", "content": "", "tool_calls": [{"id": "c1", "function": {
            "name": "load_tools", "arguments": '{"names": ["flight_finder"]}'}}]}],
        [{"type": "done", "full_content": "Loaded it."}],
        [{"type": "done", "full_content": "Hi."}],
    ])

    def fake_stream(**kw):
        sent.append(_names(kw.get("tools")))
        personas.append(kw["messages"][0]["content"])
        yield from next(script)

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("find me a flight")
    engine.handle_text_query("hello")

    assert "flight_finder" not in sent[0] and "load_tools" in sent[0]
    assert "flight_finder" in sent[1]
    assert "flight_finder" not in sent[2]
    assert "flight_finder" not in _names(engine.openai_tools)


def test_persona_no_longer_restates_tools(monkeypatch, tmp_path):
    engine = _make_lite_engine(monkeypatch)
    engine.history_file = tmp_path / "hist.json"
    seen: list[str] = []

    def fake_stream(**kw):
        seen.append(kw["messages"][0]["content"])
        yield {"type": "done", "full_content": "ok"}

    monkeypatch.setattr(daemon, "stream_deepseek_sentences", fake_stream)
    engine.handle_text_query("hi")
    for bullet in ("• open_app:", "• desktop_control:", "• screen_vision:", "• vault_knowledge:"):
        assert bullet not in seen[0]
    assert "web_search" in seen[0]
