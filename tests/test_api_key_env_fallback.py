"""Unit tests verifying that tools resolve GEMINI_API_KEY from environment without api_keys.json."""
from __future__ import annotations

from pathlib import Path

from actions import dev_agent, code_helper, computer_settings, desktop, file_processor, flight_finder, youtube_video, computer_control
from memory import config_manager


def test_gemini_api_key_from_env_without_api_keys_json(monkeypatch, tmp_path: Path):
    # Ensure config_manager uses a nonexistent CONFIG_FILE
    fake_config = tmp_path / "nonexistent_api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", fake_config)
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key-12345")

    # config_manager.get_gemini_key()
    assert config_manager.get_gemini_key() == "test-gemini-key-12345"

    # dev_agent._get_api_key()
    assert dev_agent._get_api_key() == "test-gemini-key-12345"

    # code_helper._get_api_key()
    assert code_helper._get_api_key() == "test-gemini-key-12345"

    # computer_settings._get_api_key()
    assert computer_settings._get_api_key() == "test-gemini-key-12345"

    # desktop._get_api_key()
    assert desktop._get_api_key() == "test-gemini-key-12345"

    # file_processor._get_api_key()
    assert file_processor._get_api_key() == "test-gemini-key-12345"

    # flight_finder._get_api_key()
    assert flight_finder._get_api_key() == "test-gemini-key-12345"

    # youtube_video._get_api_key()
    assert youtube_video._get_api_key() == "test-gemini-key-12345"

    # computer_control._get_api_key()
    assert computer_control._get_api_key() == "test-gemini-key-12345"


def test_gemini_api_key_empty_when_unset_and_no_file(monkeypatch, tmp_path: Path):
    fake_config = tmp_path / "nonexistent_api_keys.json"
    monkeypatch.setattr(config_manager, "CONFIG_FILE", fake_config)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("gemini_api_key", raising=False)
    monkeypatch.setattr(config_manager, "_load_env_file", lambda: {})

    assert config_manager.get_gemini_key() == ""
    assert dev_agent._get_api_key() == ""
    assert code_helper._get_api_key() == ""
    assert computer_settings._get_api_key() == ""
