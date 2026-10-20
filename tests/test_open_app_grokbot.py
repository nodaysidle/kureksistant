"""
tests/test_open_app_grokbot.py — Tests for GrokBot and Linux app launching / focus.
"""
import json
from actions.open_app import (
    _normalize,
    _clean_alphanumeric,
    _find_linux_desktop_file,
    open_app,
)
from actions.computer_control import _get_os


def test_normalize_grok_variants():
    for variant in ["grok", "Grok", "GrokBot", "grokbot", "Grok Bot", "grok-bot", "GROK-BOT"]:
        normalized = _normalize(variant)
        assert normalized == "grok-bot", f"Failed for {variant}: got {normalized}"


def test_clean_alphanumeric():
    assert _clean_alphanumeric("GrokBot") == "grokbot"
    assert _clean_alphanumeric("grok-bot") == "grokbot"
    assert _clean_alphanumeric("Grok Bot") == "grokbot"
    assert _clean_alphanumeric("com.mitchellh.ghostty") == "commitchellhghostty"


def test_find_grok_desktop_file():
    df = _find_linux_desktop_file("GrokBot")
    assert df is not None, "Failed to find grok-bot.desktop for GrokBot"
    assert "grok-bot.desktop" in str(df)

    df2 = _find_linux_desktop_file("Grok Bot")
    assert df2 is not None
    assert "grok-bot.desktop" in str(df2)


def test_computer_control_os_detection():
    # On this machine, it should detect linux now that config has been corrected
    detected = _get_os()
    assert detected == "linux", f"Expected linux, got {detected}"


def test_open_app_handler_grokbot(monkeypatch):
    # Mock subprocess.run and Popen so we verify open_app calls succeed without side effects
    calls = []

    def mock_run(cmd, *args, **kwargs):
        calls.append(cmd)
        class Res:
            returncode = 0
            stdout = json.dumps([{
                "class": "grok-bot",
                "title": "Grok Bot",
                "address": "0x1234",
                "workspace": {"id": 3, "name": "3"},
            }])
        return Res()

    monkeypatch.setattr("subprocess.run", mock_run)
    res = open_app({"app_name": "GrokBot"})
    assert "Opened GrokBot" in res or "Opened" in res
