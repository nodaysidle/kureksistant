"""Live voice: phone <-> xAI speech-to-speech bridge, with read-only tools.

Opt-in with KUREK_LIVE_VOICE=1. The phone opens ws://<host>:<KUREK_LIVE_PORT>/live
with the remote Bearer token and streams PCM16 mono 16 kHz as binary frames; the
daemon relays them to xAI's realtime API and streams PCM16 mono 24 kHz back. The
xAI key never leaves the PC. A few JSON events (ready, barge-in, transcripts,
tool calls, errors) are forwarded as text frames.

Tools: only the read-only subset in READ_ONLY_TOOLS is offered to xAI, and the
same allowlist is enforced again when a call arrives.
"""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Callable

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect
from websockets.sync.server import ServerConnection, serve

XAI_REALTIME_URL = "wss://api.x.ai/v1/realtime?model=grok-voice-3.0"
VOICE = "sal"  # Sol, same voice as the desktop TTS
IN_RATE = 16000
OUT_RATE = 24000
DEFAULT_PORT = 8791
TOOL_OUTPUT_MAX_CHARS = 4000
# Names xAI's speech recognition should expect (it hears "Koper" as "Copper").
KEYTERMS = ["Koper", "Ljubljana", "Kurek", "herdr", "Foot", "GrokBot", "Omarchy", "Hyprland"]
# Owner's town keeps arriving as an English word; fix it before the weather lookup.
CITY_FIXES = {"copper": "Koper", "cooper": "Koper"}

# Tool name -> allowed actions (None: the tool has no write actions at all).
READ_ONLY_TOOLS: dict[str, set[str] | None] = {
    "system_monitor": {"status"},
    "weather_report": None,
    "web_search": None,
    "workstation_radar": None,
    "codebase": {"search", "tree", "read", "find", "outline", "status"},
    "vault_knowledge": {"list_projects", "read", "search"},
    "manage_memory": {"recall"},
    "process_sentinel": {"list"},
}

# Server events the phone needs; everything else stays in the daemon.
FORWARD_EVENTS = {
    "session.updated",
    "input_audio_buffer.speech_started",
    "conversation.item.input_audio_transcription.completed",
    "response.output_audio_transcript.done",
    "response.done",
    "error",
}

RunTool = Callable[[str, dict], str]


def live_voice_enabled() -> bool:
    return (os.environ.get("KUREK_LIVE_VOICE") or "").strip().lower() in ("1", "true", "yes", "on")


def live_port() -> int:
    try:
        return int(os.environ.get("KUREK_LIVE_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def instructions(with_tools: bool = False) -> str:
    from core.rules import prompt_block

    name = (os.environ.get("KUREK_USER_NAME") or "").strip()
    owner = f"{name}'s" if name else "the user's"
    if with_tools:
        ability = (
            "You can use read-only tools to look things up: system status and temperatures, weather, "
            "web search, git status of projects, the vault, code, saved memory, and running watches. "
            "You cannot change anything on the computer: no opening apps, typing, files or settings. "
            "If they ask for a desktop action, say to use Kurek's normal mic button for it. "
        )
    else:
        ability = (
            "This mode is talk-only: you cannot run tools, open apps or touch the computer. "
            "If they ask for a desktop action, say to use Kurek's normal mic button for it. "
        )
    base = (
        f"You are Kurek, {owner} personal assistant, talking live by voice from their phone. "
        f"{ability}Answer in short, natural spoken sentences. Never lie; if you are not sure, say so."
    )
    rules = prompt_block()
    return f"{base}\n\n{rules}" if rules else base


def live_tools(openai_tools: list[dict]) -> list[dict]:
    """Daemon tool specs (OpenAI nested shape) -> xAI flat specs, read-only subset only."""
    out = []
    for spec in openai_tools:
        fn = spec.get("function", spec)
        name = fn.get("name")
        if name not in READ_ONLY_TOOLS:
            continue
        params = copy.deepcopy(fn.get("parameters") or {"type": "object", "properties": {}})
        allowed = READ_ONLY_TOOLS[name]
        action = (params.get("properties") or {}).get("action")
        if allowed is not None and action is not None:
            action["enum"] = sorted(allowed)
        out.append({"type": "function", "name": name, "description": fn.get("description", ""), "parameters": params})
    return out


def call_allowed(name: str, args: dict) -> bool:
    if name not in READ_ONLY_TOOLS:
        return False
    allowed = READ_ONLY_TOOLS[name]
    return allowed is None or args.get("action") in allowed


def session_update(tools: list[dict] | None = None) -> dict:
    fmt_in = {"type": "audio/pcm", "rate": IN_RATE}
    fmt_out = {"type": "audio/pcm", "rate": OUT_RATE}
    session = {
        "instructions": instructions(with_tools=bool(tools)),
        "voice": VOICE,
        "turn_detection": {"type": "server_vad"},
        "audio": {
            "input": {"format": fmt_in, "transport": "binary", "transcription": {"keyterms": KEYTERMS}},
            "output": {"format": fmt_out, "transport": "binary"},
        },
    }
    if tools:
        session["tools"] = tools
    return {"type": "session.update", "session": session}


def _answer_tool_call(ev: dict, xai, phone: ServerConnection, run_tool: RunTool | None) -> None:
    name = ev.get("name") or ""
    try:
        args = json.loads(ev.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        args = {}
    city = args.get("city")
    if name == "weather_report" and isinstance(city, str) and city.strip().lower() in CITY_FIXES:
        args["city"] = CITY_FIXES[city.strip().lower()]
    if run_tool is None or not call_allowed(name, args):
        output = f"Tool '{name}' with action '{args.get('action')}' is not available in live voice (read-only mode)."
    else:
        print(f"[Live Voice] ⚙️ {name}({args})", flush=True)
        try:
            phone.send(json.dumps({"type": "kurek.tool", "name": name, "arguments": args}))
        except ConnectionClosed:
            pass
        try:
            output = str(run_tool(name, args))
        except Exception as e:
            output = f"Tool '{name}' failed: {e}"
    try:
        xai.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": ev.get("call_id"),
                "output": output[:TOOL_OUTPUT_MAX_CHARS],
            },
        }))
        xai.send(json.dumps({"type": "response.create"}))
    except ConnectionClosed:
        pass


def _bridge(phone: ServerConnection, xai_key: str, tools: list[dict], run_tool: RunTool | None) -> None:
    peer = phone.remote_address
    print(f"[Live Voice] Session start from {peer}", flush=True)
    try:
        with connect(
            XAI_REALTIME_URL,
            additional_headers={"Authorization": f"Bearer {xai_key}"},
            max_size=None,
            open_timeout=10,
        ) as xai:
            xai.send(json.dumps(session_update(tools)))
            # Hold phone audio until xAI has applied the PCM/binary format.
            for msg in xai:
                if isinstance(msg, bytes):
                    continue
                ev = json.loads(msg)
                if ev.get("type") == "error":
                    phone.send(msg)
                    return
                if ev.get("type") == "session.updated":
                    phone.send(json.dumps({"type": "session.updated"}))
                    break

            def upstream() -> None:
                try:
                    for frame in phone:
                        if isinstance(frame, bytes):
                            xai.send(frame)
                except ConnectionClosed:
                    pass
                finally:
                    xai.close()

            threading.Thread(target=upstream, name="kurek-live-up", daemon=True).start()
            for msg in xai:
                if isinstance(msg, bytes):
                    phone.send(msg)
                    continue
                ev = json.loads(msg)
                kind = ev.get("type")
                if kind == "response.function_call_arguments.done":
                    # Off the receive loop, so a slow tool (web search) never stalls audio.
                    threading.Thread(
                        target=_answer_tool_call, args=(ev, xai, phone, run_tool), name="kurek-live-tool", daemon=True
                    ).start()
                elif kind in FORWARD_EVENTS:
                    phone.send(msg)
    except ConnectionClosed:
        pass
    except Exception as e:
        print(f"[Live Voice] Session error: {e}", flush=True)
        try:
            phone.send(json.dumps({"type": "error", "error": {"message": str(e)}}))
        except ConnectionClosed:
            pass
    finally:
        phone.close()
        print(f"[Live Voice] Session end from {peer}", flush=True)


def start_live_servers(
    hosts: list[str],
    authorize: Callable[[str | None], bool],
    openai_tools: list[dict] | None = None,
    run_tool: RunTool | None = None,
) -> None:
    """Serve /live on each host in a daemon thread. authorize() gets the Authorization header.

    With openai_tools and run_tool, the read-only subset is offered to xAI; otherwise talk-only.
    """
    from memory.config_manager import get_xai_key

    port = live_port()
    tools = live_tools(openai_tools) if (openai_tools and run_tool) else []

    def process_request(conn: ServerConnection, request):
        if request.path != "/live":
            return conn.respond(404, "Not found\n")
        # Browsers send Origin; the phone app does not. Same CSRF rule as HTTP.
        if request.headers.get("Origin"):
            return conn.respond(403, "Forbidden\n")
        if not authorize(request.headers.get("Authorization")):
            return conn.respond(401, "Unauthorized\n")
        return None

    def handler(conn: ServerConnection) -> None:
        key = get_xai_key()
        if not key:
            conn.send(json.dumps({"type": "error", "error": {"message": "No XAI_API_KEY on the PC"}}))
            conn.close()
            return
        _bridge(conn, key, tools, run_tool)

    # shortcut: binds only the hosts up at startup; a Tailscale IP that appears later
    # needs a daemon restart. Upgrade by hooking _tailscale_rebind_loop if that bites.
    for host in hosts:
        try:
            server = serve(handler, host, port, process_request=process_request, max_size=2**20)
        except OSError as e:
            print(f"[Live Voice] Could not bind ws://{host}:{port}/live: {e}", flush=True)
            continue
        threading.Thread(target=server.serve_forever, name=f"kurek-live-{host}", daemon=True).start()
        print(f"[Live Voice] 🎙️ Listening at ws://{host}:{port}/live ({len(tools)} read-only tools)", flush=True)
