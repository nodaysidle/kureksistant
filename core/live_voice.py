"""Live voice: phone <-> xAI speech-to-speech bridge (talk-only, no tools).

Opt-in with KUREK_LIVE_VOICE=1. The phone opens ws://<host>:<KUREK_LIVE_PORT>/live
with the remote Bearer token and streams PCM16 mono 16 kHz as binary frames; the
daemon relays them to xAI's realtime API and streams PCM16 mono 24 kHz back. The
xAI key never leaves the PC. A few JSON events (ready, barge-in, transcripts,
errors) are forwarded as text frames.
"""
from __future__ import annotations

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

# Server events the phone needs; everything else stays in the daemon.
FORWARD_EVENTS = {
    "session.updated",
    "input_audio_buffer.speech_started",
    "conversation.item.input_audio_transcription.completed",
    "response.output_audio_transcript.done",
    "response.done",
    "error",
}


def live_voice_enabled() -> bool:
    return (os.environ.get("KUREK_LIVE_VOICE") or "").strip().lower() in ("1", "true", "yes", "on")


def live_port() -> int:
    try:
        return int(os.environ.get("KUREK_LIVE_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def instructions() -> str:
    from core.rules import prompt_block

    name = (os.environ.get("KUREK_USER_NAME") or "").strip()
    owner = f"{name}'s" if name else "the user's"
    base = (
        f"You are Kurek, {owner} personal assistant, "
        "talking live by voice from their phone. This mode is talk-only: you cannot run tools, open apps "
        "or touch the computer. If they ask for a desktop action, say to use Kurek's normal mic button for it. "
        "Answer in short, natural spoken sentences. Never lie; if you are not sure, say so."
    )
    rules = prompt_block()
    return f"{base}\n\n{rules}" if rules else base


def session_update() -> dict:
    fmt_in = {"type": "audio/pcm", "rate": IN_RATE}
    fmt_out = {"type": "audio/pcm", "rate": OUT_RATE}
    return {
        "type": "session.update",
        "session": {
            "instructions": instructions(),
            "voice": VOICE,
            "turn_detection": {"type": "server_vad"},
            "audio": {
                "input": {"format": fmt_in, "transport": "binary"},
                "output": {"format": fmt_out, "transport": "binary"},
            },
        },
    }


def _bridge(phone: ServerConnection, xai_key: str) -> None:
    peer = phone.remote_address
    print(f"[Live Voice] Session start from {peer}", flush=True)
    try:
        with connect(
            XAI_REALTIME_URL,
            additional_headers={"Authorization": f"Bearer {xai_key}"},
            max_size=None,
            open_timeout=10,
        ) as xai:
            xai.send(json.dumps(session_update()))
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
                elif json.loads(msg).get("type") in FORWARD_EVENTS:
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


def start_live_servers(hosts: list[str], authorize: Callable[[str | None], bool]) -> None:
    """Serve /live on each host in a daemon thread. authorize() gets the Authorization header."""
    from memory.config_manager import get_xai_key

    port = live_port()

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
        _bridge(conn, key)

    # shortcut: binds only the hosts up at startup; a Tailscale IP that appears later
    # needs a daemon restart. Upgrade by hooking _tailscale_rebind_loop if that bites.
    for host in hosts:
        try:
            server = serve(handler, host, port, process_request=process_request, max_size=2**20)
        except OSError as e:
            print(f"[Live Voice] Could not bind ws://{host}:{port}/live: {e}", flush=True)
            continue
        threading.Thread(target=server.serve_forever, name=f"kurek-live-{host}", daemon=True).start()
        print(f"[Live Voice] 🎙️ Listening at ws://{host}:{port}/live", flush=True)
