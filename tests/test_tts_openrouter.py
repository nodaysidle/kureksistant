import base64
from types import SimpleNamespace

import core.tts as tts_mod


def test_xai_out_of_credits_switches_to_openrouter(monkeypatch):
    monkeypatch.setattr(
        "memory.config_manager.get_openrouter_api_key", lambda: "or-test"
    )
    monkeypatch.delenv("KUREK_TTS_MODEL", raising=False)
    monkeypatch.delenv("KUREK_TTS_VOICE", raising=False)
    engine = tts_mod.XAITTSEngine(api_key="xai-test", voice_id="sol")
    urls, bodies = [], []

    def fake_post(url, **kw):
        urls.append(url)
        bodies.append(kw["json"])
        if "x.ai" in url:
            return SimpleNamespace(status_code=403, content=b"", text="no credits")
        return SimpleNamespace(status_code=200, content=b"ID3or", text="")

    monkeypatch.setattr(engine._http, "post", fake_post)
    assert engine.synthesize("One.") == b"ID3or"
    assert engine.synthesize("Two.") == b"ID3or"
    # xAI tried once, then skipped; OpenRouter gets the same Sol voice.
    assert sum("x.ai" in u for u in urls) == 1
    assert bodies[-1]["model"] == "x-ai/grok-voice-tts-1.0"
    assert bodies[-1]["voice"] == "sal"


def test_no_keys_returns_none(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_gemini_key", lambda: "")
    monkeypatch.setattr("memory.config_manager.get_openrouter_api_key", lambda: None)
    monkeypatch.setattr("memory.config_manager.get_xai_key", lambda: None)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    engine = tts_mod.XAITTSEngine()
    assert engine.synthesize("Hi.") is None


def test_voice_env_overrides_both_routes(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_openrouter_api_key", lambda: "or")
    monkeypatch.setenv("KUREK_TTS_VOICE", "Leo")
    assert tts_mod.XAITTSEngine(api_key="x", voice_id="sal").voice_id == "leo"


def _gemini_only_engine(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_gemini_key", lambda: "gem")
    monkeypatch.setattr("memory.config_manager.get_openrouter_api_key", lambda: "or")
    monkeypatch.setattr("memory.config_manager.get_xai_key", lambda: None)
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    return tts_mod.XAITTSEngine()


def test_gemini_rotates_models_on_429(monkeypatch):
    engine = _gemini_only_engine(monkeypatch)
    urls = []

    def fake_post(url, **_kw):
        urls.append(url)
        if len(urls) < 3:  # OpenRouter fails, first Gemini model is rate limited
            return SimpleNamespace(status_code=429, content=b"", text="limit")
        data = base64.b64encode(b"RIFFwav").decode()
        parts = [{"inlineData": {"data": data}}]
        reply = {"candidates": [{"content": {"parts": parts}}]}
        return SimpleNamespace(status_code=200, json=lambda: reply)

    monkeypatch.setattr(engine._http, "post", fake_post)
    assert engine.synthesize("Hi.") == b"RIFFwav"
    assert "openrouter" in urls[0]
    assert tts_mod._GEMINI_TTS_MODELS[1] in urls[2]


def test_failed_cloud_goes_local_without_second_cloud_round(monkeypatch):
    engine = _gemini_only_engine(monkeypatch)
    calls, spoken = [], []
    monkeypatch.setattr(
        engine._http,
        "post",
        lambda url, **_kw: (
            calls.append(url) or SimpleNamespace(status_code=500, content=b"", text="")
        ),
    )
    monkeypatch.setattr(engine._fallback, "speak", lambda t, **_k: spoken.append(t))
    player = tts_mod.TTSPlayer(engine)
    player._process(player._generation, "Hi.", True, None)
    assert spoken == ["Hi."]
    assert len(calls) == 1 + len(tts_mod._GEMINI_TTS_MODELS)


def test_spd_say_without_synthesizer_stays_silent(monkeypatch):
    ran = []
    monkeypatch.setattr(tts_mod.sys, "platform", "linux")
    monkeypatch.setattr(tts_mod.shutil, "which", lambda _n: "/usr/bin/spd-say")

    def fake_run(cmd, **_kw):
        ran.append(cmd)
        return SimpleNamespace(stdout="OUTPUT MODULES\n")

    monkeypatch.setattr(tts_mod.subprocess, "run", fake_run)
    tts_mod.SystemSpeechEngine().speak("Hi.")
    assert ran == [["spd-say", "-O"]]
