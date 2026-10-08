"""
Drop-in JARVIS plugin: Safari YouTube & Media Control for macOS.
Controls playback, pause, volume, and search for YouTube in Safari via AppleScript.
"""
import subprocess
import urllib.parse

PLUGIN = {
    "name": "safari_youtube_control",
    "description": (
        "Controls YouTube playback in Safari on macOS. "
        "Use this to: play, pause, toggle playback, seek forward/back, next video, "
        "or open a new video in Safari. "
        "Actions: 'play', 'pause', 'toggle', 'next', 'mute', 'search'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'play' | 'pause' | 'toggle' | 'next' | 'mute' | 'search' (default: toggle)"
            },
            "query": {
                "type": "STRING",
                "description": "Video or song to search and play (used when action is 'search' or 'play')"
            }
        },
        "required": []
    }
}

def _run_applescript(script: str) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters or {}
    action = (params.get("action") or "toggle").lower().strip()
    query = (params.get("query") or "").strip()

    if player:
        player.write_log(f"[SafariYouTube] Action: {action} query: '{query}'")

    if action in ("play", "search") and query:
        # Search & open video with autoplay in Safari
        encoded = urllib.parse.quote_plus(query)
        # Try to open direct watch url via search
        search_url = f"https://www.youtube.com/results?search_query={encoded}"
        subprocess.Popen(["open", "-a", "Safari", search_url])
        return f"Searching and opening '{query}' on YouTube in Safari, sir."

    # Safari media controls
    if action in ("toggle", "play", "pause"):
        script = '''
        tell application "System Events"
            if exists (process "Safari") then
                tell application "Safari" to activate
                key code 49 -- spacebar (toggles YouTube video)
                return "Playback toggled"
            else
                return "Safari is not currently running"
            end if
        end tell
        '''
        res = _run_applescript(script)
        return f"{res}, sir."

    elif action == "next":
        script = '''
        tell application "System Events"
            if exists (process "Safari") then
                tell application "Safari" to activate
                -- Shift + N triggers next video on YouTube
                key down shift
                key code 45 -- n
                key up shift
                return "Skipped to next video"
            else
                return "Safari is not running"
            end if
        end tell
        '''
        res = _run_applescript(script)
        return f"{res}, sir."

    elif action == "mute":
        script = '''
        tell application "System Events"
            if exists (process "Safari") then
                tell application "Safari" to activate
                key code 46 -- m (toggles YouTube mute)
                return "Toggled mute"
            else
                return "Safari is not running"
            end if
        end tell
        '''
        res = _run_applescript(script)
        return f"{res}, sir."

    return f"Action '{action}' processed for Safari YouTube."
