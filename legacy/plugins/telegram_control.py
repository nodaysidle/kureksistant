"""
Drop-in JARVIS plugin: Telegram integration for macOS.
Opens Telegram, focuses chat, or composes messages via Telegram URI scheme.
"""
import subprocess
import urllib.parse

PLUGIN = {
    "name": "telegram_control",
    "description": (
        "Controls Telegram on macOS. Use this to open Telegram, focus the app, "
        "or compose and send a message. "
        "Actions: 'open', 'compose'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'open' (launch/focus Telegram) | 'compose' (draft a message)"
            },
            "message": {
                "type": "STRING",
                "description": "Text message content to draft or send"
            }
        },
        "required": []
    }
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "open").lower().strip()
    msg = (params.get("message") or "").strip()

    if player:
        player.write_log(f"[Telegram] Action: {action}")

    if action == "compose" and msg:
        encoded = urllib.parse.quote(msg)
        tg_url = f"tg://msg?text={encoded}"
        try:
            subprocess.Popen(["open", tg_url])
            return f"Opening Telegram with draft message: '{msg}', sir."
        except Exception as e:
            return f"Failed to open Telegram link: {e}"

    try:
        subprocess.Popen(["open", "-a", "Telegram"])
        return "Telegram opened, sir."
    except Exception as e:
        return f"Could not launch Telegram: {e}"
