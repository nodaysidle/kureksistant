"""Live voice bridge: auth and config, no network to xAI."""
import json

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from core import live_voice
from memory import config_manager

PORT = 18792  # throwaway, never the live 8791


@pytest.fixture(scope="module")
def server():
    mp = pytest.MonkeyPatch()
    mp.setenv("KUREK_LIVE_PORT", str(PORT))
    mp.setattr(config_manager, "get_xai_key", lambda: None)
    live_voice.start_live_servers(["127.0.0.1"], lambda h: h == "Bearer good")
    yield
    mp.undo()


def test_rejects_missing_token(server) -> None:
    with pytest.raises(InvalidStatus) as e:
        connect(f"ws://127.0.0.1:{PORT}/live")
    assert e.value.response.status_code == 401


def test_rejects_browser_origin(server) -> None:
    with pytest.raises(InvalidStatus) as e:
        connect(
            f"ws://127.0.0.1:{PORT}/live",
            additional_headers={"Authorization": "Bearer good"},
            origin="http://evil.example",
        )
    assert e.value.response.status_code == 403


def test_authorized_without_xai_key_gets_error(server) -> None:
    with connect(f"ws://127.0.0.1:{PORT}/live", additional_headers={"Authorization": "Bearer good"}) as ws:
        ev = json.loads(ws.recv(timeout=5))
    assert ev["type"] == "error"


def test_session_update_is_binary_pcm_with_sol_voice() -> None:
    s = live_voice.session_update()["session"]
    assert s["voice"] == "sal"
    assert s["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 16000}
    assert s["audio"]["input"]["transport"] == "binary"
    assert "Koper" in s["audio"]["input"]["transcription"]["keyterms"]
    assert s["audio"]["output"]["format"]["rate"] == 24000
    assert "talk-only" in s["instructions"]


def test_live_tools_keep_only_read_only_and_narrow_actions() -> None:
    specs = [
        {"type": "function", "function": {"name": "desktop_control", "parameters": {}}},
        {"type": "function", "function": {"name": "vault_knowledge", "description": "v", "parameters": {
            "type": "object", "properties": {"action": {"type": "string", "enum": ["read", "search", "add_inbox", "list_projects"]}},
        }}},
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}},
    ]
    tools = live_voice.live_tools(specs)
    assert [t["name"] for t in tools] == ["vault_knowledge", "web_search"]
    assert tools[0]["type"] == "function" and "function" not in tools[0]
    assert tools[0]["parameters"]["properties"]["action"]["enum"] == ["list_projects", "read", "search"]
    assert specs[1]["function"]["parameters"]["properties"]["action"]["enum"][2] == "add_inbox"  # input untouched


def test_call_allowed_enforces_allowlist_again() -> None:
    assert live_voice.call_allowed("web_search", {"query": "x"})
    assert live_voice.call_allowed("manage_memory", {"action": "recall"})
    assert not live_voice.call_allowed("manage_memory", {"action": "remember"})
    assert not live_voice.call_allowed("system_monitor", {"action": "set_threshold"})
    assert not live_voice.call_allowed("vault_knowledge", {})
    assert not live_voice.call_allowed("run", {"action": "read"})


def test_weather_city_copper_becomes_koper() -> None:
    seen = {}
    sent = []

    class _WS:
        def send(self, m):
            sent.append(m)

    def run(name, args):
        seen.update(args)
        return "ok"

    ev = {"name": "weather_report", "call_id": "c1", "arguments": '{"city": "Copper"}'}
    live_voice._answer_tool_call(ev, _WS(), _WS(), run)
    assert seen["city"] == "Koper"
    assert json.loads(sent[-2])["item"]["call_id"] == "c1"
