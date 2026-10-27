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
    assert s["audio"]["input"] == {"format": {"type": "audio/pcm", "rate": 16000}, "transport": "binary"}
    assert s["audio"]["output"]["format"]["rate"] == 24000
    assert "talk-only" in s["instructions"]
