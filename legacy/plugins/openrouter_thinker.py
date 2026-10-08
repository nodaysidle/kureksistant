"""
Drop-in JARVIS plugin: OpenRouter Deep Reasoning & Model Routing.
Uses OpenRouter (DeepSeek-V3, DeepSeek-R1, Gemini 2.5 Flash, etc.) for complex queries,
coding questions, technical architecture, and deep analysis.
"""
import json
import requests
from pathlib import Path
from memory.config_manager import get_openrouter_key, get_openrouter_model, get_plugin_setting

PLUGIN = {
    "name": "openrouter_thinker",
    "description": (
        "Queries advanced reasoning models via OpenRouter (such as DeepSeek-V3, "
        "DeepSeek-R1, or Gemini Flash). "
        "Use this for deep technical questions, complex coding, detailed architecture explanations, "
        "or whenever the user asks for deep thinking or mentions OpenRouter/DeepSeek."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {
                "type": "STRING",
                "description": "The complex question, topic, or coding task to solve"
            },
            "model": {
                "type": "STRING",
                "description": "Optional model override (e.g. 'deepseek/deepseek-chat', 'deepseek/deepseek-r1')"
            }
        },
        "required": ["prompt"]
    }
}

PLUGIN_SETTINGS = {
    "title": "OpenRouter AI Models",
    "namespace": "openrouter",
    "fields": [
        {
            "key": "api_key",
            "type": "password",
            "label": "OpenRouter API Key",
            "placeholder": "sk-or-v1-..."
        },
        {
            "key": "model",
            "type": "choice",
            "label": "Primary Model",
            "options": [
                "deepseek/deepseek-chat",
                "deepseek/deepseek-r1",
                "google/gemini-2.5-flash",
                "openai/gpt-4o-mini",
                "anthropic/claude-3.7-sonnet"
            ],
            "default": "deepseek/deepseek-chat"
        }
    ]
}

def _get_key() -> str:
    # 1. Check plugin settings first
    k = get_plugin_setting("openrouter", "api_key")
    if k:
        return str(k).strip()
    # 2. Check top-level config
    return get_openrouter_key()

def _get_model(preferred: str = "") -> str:
    if preferred:
        return preferred
    m = get_plugin_setting("openrouter", "model")
    if m:
        return str(m).strip()
    return get_openrouter_model() or "deepseek/deepseek-chat"

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    user_prompt = (params.get("prompt") or "").strip()
    preferred_model = (params.get("model") or "").strip()

    if not user_prompt:
        return "Please specify a question or task to think through, sir."

    api_key = _get_key()
    if not api_key:
        msg = "OpenRouter API key is not set. Please add your key in ⚙ Settings → OpenRouter AI Models or in config/api_keys.json."
        if player:
            player.write_log(f"SYS: {msg}")
        return msg

    model = _get_model(preferred_model)
    if player:
        player.write_log(f"[OpenRouter] Querying {model}...")

    messages = [
        {"role": "system", "content": "You are JARVIS, an ultra-competent, concise, and articulate AI assistant. Answer clearly with minimal fluff."},
        {"role": "user", "content": user_prompt}
    ]

    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/FatihMakes/Mark-LIII",
                "X-Title": "JARVIS"
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.3
            },
            timeout=30
        )
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            if player:
                player.show_content(f"REASONING — {model.split('/')[-1].upper()}", content)
            return content
        else:
            return f"OpenRouter returned status {resp.status_code}: {resp.text[:120]}"
    except Exception as e:
        return f"OpenRouter query failed: {e}"
