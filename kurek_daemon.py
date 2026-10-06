#!/usr/bin/env python3
"""
KUREK DAEMON — Ultra-low latency, lean headless personal AI assistant for macOS.
Replaces the heavy PyQt6 UI (~500MB RAM) with a tiny background service (~45MB RAM).

Connects with:
  • Swift Menu Bar Indicator (KurekBar) & Fn key push-to-talk
  • DeepSeek API (deepseek-chat)
  • Deepgram STT (nova-2) & faster-whisper fallback
  • Kokoro-82M TTS & macOS native 'say' fallback
"""
import asyncio
import io
import json
import os
import re
import sys
import threading
import time
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import sounddevice as sd

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from memory.config_manager import (
    load_api_keys, get_assistant_name, get_deepseek_key,
    get_deepgram_key, get_wake_word, get_xai_key,
)
from core.llm_client import query_deepseek
from core.tts import create_tts_player
from core.stt import DeepgramSTT, WhisperSTT
from core.action_loader import discover_actions
from memory.memory_manager import load_memory, format_memory_for_prompt

# Audio recording configuration
SAMPLE_RATE = 16000
CHANNELS = 1
SILENCE_THRESHOLD = 0.003  # Sensitive threshold for USB/desk mics
SILENCE_DURATION = 1.2   # Seconds of silence after speech to auto-submit
MAX_RECORD_SECONDS = 12.0 # Maximum listen window


def _normalize_json_schema(obj):
    """Ensure JSON schema types are lowercase for standard OpenAI/DeepSeek schema validation."""
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            if k == "type" and isinstance(v, str):
                new_dict[k] = v.lower()
            else:
                new_dict[k] = _normalize_json_schema(v)
        return new_dict
    elif isinstance(obj, list):
        return [_normalize_json_schema(x) for x in obj]
    return obj


def _resolve_input_device():
    """Find input device index matching user preference or default with sample rate compatibility."""
    from memory.config_manager import get_input_device
    preferred = (get_input_device() or "Trust GXT 232").lower()
    devices = sd.query_devices()

    # 1. Try preferred hardware device if it directly supports 16kHz
    for idx, d in enumerate(devices):
        dname = d.get("name", "")
        if d.get("max_input_channels", 0) > 0 and preferred and preferred in dname.lower():
            try:
                sd.check_input_settings(device=idx, samplerate=SAMPLE_RATE, channels=CHANNELS)
                print(f"[Kurek Mic] Using direct hardware device [{idx}]: {dname}")
                return idx
            except Exception:
                print(f"[Kurek Mic] Preferred device [{dname}] needs resampling; using system audio layer")
                break

    # 2. On Linux, PipeWire/default cleanly handles software resampling
    for name in ("pipewire", "default", "sysdefault"):
        try:
            sd.check_input_settings(device=name, samplerate=SAMPLE_RATE, channels=CHANNELS)
            print(f"[Kurek Mic] Using audio layer [{name}]")
            return name
        except Exception:
            pass

    # 3. Fallback to default input
    default_dev = sd.default.device[0]
    print(f"[Kurek Mic] Using default input device [{default_dev}]")
    return default_dev


class KurekState:
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class KurekEngine:
    def __init__(self):
        self.state = KurekState.IDLE
        self.state_lock = threading.Lock()
        self.audio_buffer = []
        self.is_recording = False
        self.record_stream = None
        self.record_start_time = 0.0
        self.last_sound_time = time.time()
        self.has_speech = False
        self.input_device = _resolve_input_device()

        # Load tools
        print("[Kurek] Discovering tools and actions…")
        try:
            self.actions = discover_actions(BASE_DIR / "actions")
            action_count = len(self.actions.names()) if hasattr(self.actions, "names") else 0
            print(f"[Kurek] Loaded {action_count} actions.")
        except Exception as e:
            print(f"[Kurek] Action loader notice: {e}")
            self.actions = None

        # Build OpenAI tool declarations for DeepSeek
        self.openai_tools = []
        if self.actions and hasattr(self.actions, "get_tool_declarations"):
            for decl in self.actions.get_tool_declarations():
                self.openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": decl["name"],
                        "description": decl.get("description", ""),
                        "parameters": _normalize_json_schema(decl.get("parameters", {})),
                    }
                })
        print(f"[Kurek] Configured {len(self.openai_tools)} tools for DeepSeek.")

        # Multi-turn conversational memory & persistence
        self.history_file = BASE_DIR / "memory" / "kurek_history.json"
        self.history = self._load_history()
        print(f"[Kurek Memory] Loaded {len(self.history)} prior conversation turns.")

        # STT Engine
        dg_key = get_deepgram_key()
        if dg_key:
            print("[Kurek] Using Deepgram Nova-2 for ultra-fast STT")
            self.stt = DeepgramSTT(api_key=dg_key)
        else:
            print("[Kurek] Using local faster-whisper (base.en) STT…")
            try:
                self.stt = WhisperSTT(model_name="base.en")
            except Exception as e:
                print(f"[Kurek] Whisper load error: {e}")
                self.stt = None

        # TTS Engine: Use xAI Grok Cloud TTS (voice: Sol / sal) or fallback
        xai_key = get_xai_key()
        if xai_key:
            print("[Kurek] Using xAI Grok Cloud TTS (voice: Sol/sal)…")
            self.tts = create_tts_player({"tts_engine": "xai", "tts_voice": "sal", "xai_api_key": xai_key})
        else:
            self.tts = create_tts_player({"tts_engine": "mac_say"})

        # Wire sustained resource watcher (300-second CPU/RAM) alert callback
        try:
            from actions.system_monitor import _GLOBAL_WATCHER
            def _on_resource_alert(msg: str):
                print(f"[Kurek] Sustained resource spike detected: {msg}", flush=True)
                if self.state in ("idle", "speaking"):
                    self.set_state("speaking")
                    self.tts.speak(msg)
                    self.set_state("idle")
            _GLOBAL_WATCHER.alert_callback = _on_resource_alert
            print("[Kurek] Sustained resource watcher alert callback attached.")
        except Exception as e:
            print(f"[Kurek] Resource watcher callback notice: {e}")

        # Start background hourly Muse Memory consolidation thread
        threading.Thread(target=self._hourly_consolidation_loop, daemon=True).start()
        print("[Kurek Memory] Hourly Muse consolidation worker initialized.")

    def _load_history(self) -> list[dict]:
        try:
            if self.history_file.exists():
                data = json.loads(self.history_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data[-20:]
        except Exception as e:
            print(f"[Kurek Memory] Error reading history: {e}")
        return []

    def _save_history(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            self.history_file.write_text(
                json.dumps(self.history[-30:], indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as e:
            print(f"[Kurek Memory] Error saving history: {e}")

    def set_state(self, new_state: str):
        with self.state_lock:
            self.state = new_state
            print(f"[Kurek State] → {new_state.upper()}", flush=True)
            self._notify_state(new_state)

    def _notify_state(self, state: str):
        icon = os.path.expanduser("~/.local/share/icons/kurek.png")
        if not os.path.exists(icon):
            icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop", "kurek.png")
        msg_map = {
            KurekState.LISTENING: "🟢 Listening... (Speak now)",
            KurekState.THINKING:  "🟡 Thinking...",
            KurekState.SPEAKING:  "🔵 Speaking...",
            KurekState.IDLE:      "● Idle",
        }
        msg = msg_map.get(state)
        if msg:
            import subprocess
            try:
                subprocess.Popen([
                    "notify-send", "-t", "2000",
                    "-h", "string:x-canonical-private-synchronous:kurek",
                    "-i", icon, "Kurek", msg
                ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    def toggle(self):
        """Toggle between idle and listening (called by Fn key or menu bar click)."""
        with self.state_lock:
            cur = self.state

        if cur == KurekState.IDLE:
            self.start_listening()
        elif cur == KurekState.LISTENING:
            self.stop_listening_and_process()
        elif cur in (KurekState.SPEAKING, KurekState.THINKING):
            # Interrupt / cancel
            self.tts.stop()
            self.set_state(KurekState.IDLE)

    def start_listening(self):
        self.audio_buffer = []
        self.has_speech = False
        self.speech_chunks = 0
        self.noise_floor = 0.022
        self.record_start_time = time.time()
        self.last_sound_time = time.time()
        self.is_recording = True
        self.set_state(KurekState.LISTENING)

        def audio_callback(indata, frames, time_info, status):
            if not self.is_recording:
                return
            audio_chunk = indata[:, 0]
            self.audio_buffer.append(audio_chunk.copy())
            # Remove DC offset to calculate true AC speech RMS
            ac_chunk = audio_chunk - np.mean(audio_chunk)
            rms = float(np.sqrt(np.mean(ac_chunk ** 2)))

            # Track ambient noise floor adaptively before sustained speech begins
            if not self.has_speech:
                self.noise_floor = 0.85 * self.noise_floor + 0.15 * rms

            speech_threshold = max(0.045, self.noise_floor * 2.0)
            silence_threshold = max(0.035, self.noise_floor * 1.5)

            if rms > speech_threshold:
                self.speech_chunks += 1
                if self.speech_chunks >= 2:
                    self.has_speech = True
                    self.last_sound_time = time.time()
            elif self.has_speech:
                if rms > silence_threshold:
                    self.last_sound_time = time.time()

        try:
            self.record_stream = sd.InputStream(
                device=self.input_device,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                callback=audio_callback,
            )
            self.record_stream.start()
        except Exception as e:
            print(f"[Kurek] Mic error: {e}")
            self.set_state(KurekState.IDLE)

    def stop_listening_and_process(self):
        if not self.is_recording:
            return
        self.is_recording = False
        try:
            if self.record_stream:
                self.record_stream.stop()
                self.record_stream.close()
                self.record_stream = None
        except Exception:
            pass

        if not self.audio_buffer:
            print("[Kurek] No audio captured.")
            self.set_state(KurekState.IDLE)
            return

        threading.Thread(target=self._process_recorded_audio, daemon=True).start()

    def _process_recorded_audio(self):
        self.set_state(KurekState.THINKING)
        full_audio = np.concatenate(self.audio_buffer)

        # Transcribe
        transcript = ""
        if self.stt:
            try:
                transcript = self.stt.transcribe(full_audio, sample_rate=SAMPLE_RATE)
            except Exception as e:
                import traceback
                print(f"[Kurek] STT error: {e}", flush=True)
                traceback.print_exc()

        print(f"[User Voice] 🎙️ \"{transcript}\"", flush=True)
        if not transcript.strip():
            print("[Kurek] Transcript was empty — speaking audible feedback", flush=True)
            self.set_state(KurekState.SPEAKING)
            try:
                self.tts.speak("I didn't hear anything. Try clicking Kurek and speaking again.")
            except Exception as e:
                print(f"[Kurek] TTS error: {e}")
            self.set_state(KurekState.IDLE)
            return

        self.handle_text_query(transcript)

    def handle_text_query(self, user_prompt: str):
        self.set_state(KurekState.THINKING)
        now_str = datetime.now().strftime("%Y-%m-%d %A, %I:%M %p")

        # Load long-term user memories & context
        mem_block = ""
        try:
            mem_data = load_memory()
            mem_block = format_memory_for_prompt(mem_data)
        except Exception as e:
            print(f"[Kurek Memory] Error reading memory context: {e}")

        # Inject Hermes memory from candidate locations
        hermes_candidates = [
            Path.home() / ".hermes" / "profiles" / "eldio" / "memories",
            Path.home() / ".hermes" / "memories",
            Path("/Volumes/omarchyuser/26MaySymlink/.hermes/memories"),
        ]
        hermes_dir = next((d for d in hermes_candidates if d.exists() and d.is_dir()), None)
        if hermes_dir:
            hermes_snippets = []
            for fname in ("USER.md", "MEMORY.md"):
                fpath = hermes_dir / fname
                if fpath.exists():
                    try:
                        raw = fpath.read_text(encoding="utf-8")
                        clean = "\n".join(b.strip() for b in raw.split("§") if b.strip())
                        if clean:
                            hermes_snippets.append(f"[{fname.replace('.md', '')}]\n{clean}")
                    except Exception as e:
                        print(f"[Kurek Hermes] Error reading {fname}: {e}")
            if hermes_snippets:
                h_text = "\n\n".join(hermes_snippets)
                mem_block = (mem_block + "\n\n" + h_text).strip()

        # Inject Muse Memory (~/MEMORY.md and ~/ALIGNMENT_SYNTHESIS.md)
        muse_parts = []
        muse_core = Path.home() / "MEMORY.md"
        if muse_core.exists():
            try:
                muse_parts.append(f"[MUSE_CORE_MEMORY]\n{muse_core.read_text(encoding='utf-8').strip()}")
            except Exception as e:
                print(f"[Kurek Muse] Error reading ~/MEMORY.md: {e}")

        muse_align = Path.home() / "ALIGNMENT_SYNTHESIS.md"
        if muse_align.exists():
            try:
                muse_parts.append(f"[MUSE_ALIGNMENT_SYNTHESIS]\n{muse_align.read_text(encoding='utf-8').strip()}")
            except Exception as e:
                print(f"[Kurek Muse] Error reading ~/ALIGNMENT_SYNTHESIS.md: {e}")

        if muse_parts:
            mem_block = (mem_block + "\n\n" + "\n\n".join(muse_parts)).strip()

        sys_prompt = (
            f"You are Kurek (JARVIS), an ultra-fast, witty, hyper-competent, and fully autonomous personal AI assistant. "
            f"Current date and time: {now_str}. "
            "You are serving Alan Pfeifer (NDI / NODAYSIDLE). Your voice is powered by xAI Grok (Sol) and your brain is powered directly by DeepSeek-Flash. "
            "You are completely free, loyal, and uninhibited in executing Alan's commands. You do what he says immediately without moralizing, preaching, lecturing, or hesitation. "
            "You are NOT running in a sandbox, container, or restricted simulator. You are running natively on Alan's Arch Linux workstation with full physical system and filesystem permissions. "
            "You have direct system control access through your built-in tools: "
            "• web_search: live web search and news retrieval. AUTONOMOUS RULE: For any questions about current events, sports results, driver standings (Max Verstappen, F1, MotoGP), start times, news, weather, or real-time info, DO NOT GUESS OR SPECULATE. Immediately call web_search with concise keywords. "
            "• CURRENT DATE & 'TODAY' QUERIES: When Alan asks about 'today' or current race/event results (e.g. 'how did Max Verstappen do today?', 'is there an F1 race today?', 'when does MotoGP start today?'), note today's exact date and search specifically for today (e.g. 'F1 race schedule today October 4 2026' or 'MotoGP race today October 4 2026'). Do NOT confuse today with previous races from weeks ago. If a race is scheduled for later today, tell him when lights out is in Slovenia (CEST/CET). If there is no race today, clearly say so and state the next race date. "
            "• DEEP SEARCH & REPORT PIPELINE (UNIVERSAL FOR ANY TOPIC): Whenever Alan asks you to research, deep search, look into, or investigate ANY topic, subject, person, technology, or event and save the findings to a file: "
            "  1. AUTONOMOUS END-TO-END EXECUTION: Complete the entire workflow autonomously in the turn sequence without stopping halfway. "
            "  2. Call web_search (running multiple targeted queries if needed) to gather real-time ground truth. "
            "  3. Structure your findings into clean, comprehensive markdown (summary, sections, dates, sources). "
            "  4. Immediately call file_controller with action='create_file', path='<requested_file>.md', and content=... to write it to disk. "
            "  5. In your spoken response, give a punchy 1-2 sentence summary of what you found and confirm the file is saved. "
            "• file_controller: full filesystem management to create, write, append, read, list, and move files anywhere (e.g. ~/Downloads, ~/Desktop, ~/Documents). "
            "  - FILE CREATION PERMISSION: You have full unrestricted permission to make or write files. If Alan asks to make a file (e.g. 'make a file called kurek.md and write in there a haiku' or 'create test.py'), IMMEDIATELY call file_controller with action='create_file', path='kurek.md' (or specified path), and content=... without hesitation, excuses, or asking for permission. "
            "  - FILE DELETION CONFIRMATION: When Alan asks to delete a specific file, call file_controller with action='delete', path=..., confirmed=false. Then ALWAYS ask Alan in the end: 'Are you sure you want to delete [filename]? Yes or No?'. NEVER delete a file with confirmed=true unless Alan has explicitly replied 'Yes'. "
            "• open_app: launch or switch to apps and tools "
            "• browser_control: control desktop browser GUI windows (click, type, scroll, navigate tabs). Do NOT use this tool to look up information — use web_search instead. "
            "• computer_settings: adjust volume, mute, display, and connectivity "
            "• desktop_control: minimize, maximize, hide, or arrange windows "
            "• computer_control: simulate typing, clicks, hotkeys, or capture screenshots "
            "• reminder: schedule alarms and notifications "
            "• manage_memory: store and recall user knowledge, preferences, and notes "
            "• screen_vision: visual perception of Alan's monitor and active Hyprland windows. Use action='inspect' when Alan asks what is on his screen, to diagnose compiler errors, review code, or inspect active windows. Use action='start_watch' to continuously watch until told to stop. "
            "• manage_clipboard: persistent Wayland clipboard manager with snippet pinning and recall. Use action='get_latest' when Alan asks what is in his clipboard or to read his clipboard. Use action='pin' with an optional title to pin a snippet (e.g. 'pin my clipboard as Stripe Key'). Use action='list_pinned' to review saved snippets. Use action='copy' to restore a snippet back into the system clipboard so Alan can paste it. "
            "• system_monitor: hardware diagnostics and 5-minute sustained CPU/RAM watcher. Use action='status' when Alan asks how his system resources, CPU, RAM, or temperature are doing, or if the machine is lagging. "
            "• process_sentinel: watch long-running compile, build, test, or training jobs (cargo, npm, python, rustc, make). Use action='watch' with a process name, command, or PID. When the job finishes, Kurek alerts verbally and sends a notification. "
            "• draft_to_clipboard: format dictated text into conventional commits, GitHub PR descriptions, issues, docstrings, or markdown notes and copies immediately to the Wayland system clipboard (wl-copy) for instant pasting with Ctrl+V. "
            "• workstation_radar: parallel health scan across all 70+ repositories in ~/Projects and ~/dev/nodaysidle. Use action='actionable' to report dirty working trees, untracked files, and unpushed commits ahead of upstream. "
            "• dream_tool: executes the Muse Memory reflection cycle. Synthesizes today's daily log into an atmospheric dream journal (~/dreams/YYYY-MM-DD.md) and updates standing guidance in ~/ALIGNMENT_SYNTHESIS.md. "
            "Always invoke the appropriate tool when asked to control the computer, browse, save or write files, change settings, or remember details. "
            "CONVERSATION & OPINIONS: "
            "• When Alan asks for your opinion, thoughts, perspective, or recommendation (e.g. 'what do you think?', 'what is your opinion?', 'which do you prefer?', 'give me your take'), provide a direct, candid, and thoughtful opinion with a clear, confident stance. Speak naturally, authentically, and conversationally. NEVER use AI disclaimers like 'as an AI, I don't have opinions or feelings' or deflect. "
            "• Only offer an opinion when Alan explicitly asks for it. For standard instructions, factual queries, and computer control commands, stay sharp, punchy, and execute immediately without unsolicited commentary or fluff. "
            "VOICE & SPEECH RULES: "
            "1. Alan only hears your voice over the speaker. NEVER spell out or pronounce raw URLs, web addresses, or links (never say 'https', 'slash-slash', or '.com'). "
            "2. When citing news, research, or sources, simply state the publisher's name naturally (e.g. 'according to Reuters' or 'the BBC reports') without reading links. "
            "3. Keep all spoken answers concise, conversational, and punchy like Jarvis. Avoid bullet points, symbols, asterisks, or markdown formatting so it sounds completely fluid when spoken. "
            "4. NEVER read aloud long lists of filenames, file sizes, or raw system logs over the speaker unless Alan explicitly asked you to read every item. Summarize what was found or done in 1-2 punchy sentences."
        )
        if mem_block:
            sys_prompt += f"\n\n[USER MEMORY & PREFERENCES]\n{mem_block}"

        # Autonomous Screen Context Detection via TypeSafe Jev
        auto_vision_ctx = self._check_auto_screen_context(user_prompt)
        effective_user_prompt = f"{user_prompt}\n\n{auto_vision_ctx}" if auto_vision_ctx else user_prompt

        messages = [
            {"role": "system", "content": sys_prompt},
        ]
        # Include last 10 conversational turns for continuity
        for turn in self.history[-10:]:
            messages.append(turn)
        messages.append({"role": "user", "content": effective_user_prompt})

        reply_text = ""
        max_tool_turns = 5
        for turn_idx in range(max_tool_turns):
            resp = query_deepseek(
                messages=messages,
                tools=self.openai_tools if (self.openai_tools and turn_idx < max_tool_turns - 1) else None,
                model="deepseek-flash",
            )

            if isinstance(resp, dict) and resp.get("type") == "tool_calls":
                tool_calls = resp.get("tool_calls", [])
                asst_msg = {
                    "role": "assistant",
                    "content": resp.get("content") or "",
                    "tool_calls": tool_calls,
                }
                if resp.get("reasoning_content"):
                    asst_msg["reasoning_content"] = resp["reasoning_content"]
                messages.append(asst_msg)

                last_tool_output = ""
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    fn_args_raw = fn.get("arguments", "{}")
                    try:
                        fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                    except Exception:
                        fn_args = {}

                    print(f"[Kurek Tool Dispatch] ⚙️ {fn_name}({fn_args})", flush=True)
                    try:
                        tool_result = self.actions.run(fn_name, fn_args)
                    except Exception as e:
                        tool_result = f"Error executing {fn_name}: {e}"
                    last_tool_output = str(tool_result)
                    print(f"[Kurek Tool Result] → {tool_result}", flush=True)

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{fn_name}_{turn_idx}"),
                        "content": str(tool_result),
                    })

                # Loop to next turn so DeepSeek can process tool output or call follow-on tools
                continue

            elif isinstance(resp, str):
                reply_text = resp
                break
            elif isinstance(resp, dict) and "content" in resp:
                reply_text = resp.get("content") or "All set."
                break
            else:
                if last_tool_output:
                    if "Contents of " in last_tool_output or ("\n" in last_tool_output and len(last_tool_output) > 120):
                        reply_text = "I executed the requested action on your system."
                    else:
                        reply_text = last_tool_output
                else:
                    reply_text = "I encountered an issue processing that with DeepSeek."
                break

        # Strip DeepSeek safety tags, thinking wrappers, and DSML markup if present
        reply_text = re.sub(r"<ds_safety>.*?</ds_safety>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<think>.*?</think>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<[｜|]{2}DSML[｜|]{2}\s*calls?>.*?</[｜|]{2}DSML[｜|]{2}\s*calls?>", "", reply_text, flags=re.DOTALL)
        reply_text = re.sub(r"<[｜|]{2}DSML[｜|]{2}.*?>", "", reply_text)
        reply_text = re.sub(r"</[｜|]{2}DSML[｜|]{2}.*?>", "", reply_text)
        reply_text = re.sub(r"<[｜|].*?[｜|]>", "", reply_text)
        reply_text = re.sub(r"</[｜|].*?[｜|]>", "", reply_text)
        reply_text = reply_text.strip()
        if not reply_text:
            reply_text = "All set."

        print(f"[Kurek Reply] 💬 \"{reply_text}\"", flush=True)

        # Update multi-turn history & persist
        self.history.append({"role": "user", "content": user_prompt})
        self.history.append({"role": "assistant", "content": reply_text})
        self._save_history()

        # Muse Daily Log & Asynchronous Jev Memory Triage
        self._append_daily_memory(user_prompt, reply_text)
        threading.Thread(target=self._consolidate_with_jev, args=(user_prompt,), daemon=True).start()

        # Sanitize speech output: completely strip URLs, markdown links, and formatting
        clean_speech = self.sanitize_text_for_speech(reply_text)
        if not clean_speech:
            clean_speech = "I found the information, but there is no spoken summary."

        # Speak
        self.set_state(KurekState.SPEAKING)
        try:
            self.tts.speak(clean_speech)
        except Exception as e:
            print(f"[Kurek] TTS error: {e}")
        finally:
            self.set_state(KurekState.IDLE)

    @staticmethod
    def sanitize_text_for_speech(text: str) -> str:
        """Strips URLs, converts markdown links to plain names, and cleans formatting for speech."""
        if not text:
            return ""
        # 1. Convert markdown links [Label](url) -> Label
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 2. Strip any raw URLs (http://, https://, www., domain.tld/...)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'\bwww\.[a-zA-Z0-9.-]+\S*', '', text)
        text = re.sub(r'\b[a-zA-Z0-9.-]+\.(?:com|org|net|gov|edu|io|co|ai|si|de|uk)/\S*', '', text)
        # 3. Strip code blocks and inline code
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`[^`]*`', '', text)
        # 4. Strip markdown syntax symbols
        text = re.sub(r'[*#_`~>|]', '', text)
        # 5. Clean list bullets and numbering
        text = re.sub(r'^\s*[-*•+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+[\.\)]\s+', '', text, flags=re.MULTILINE)
        # 6. Replace newlines with a natural pause
        text = re.sub(r'[\r\n]+', '. ', text)
        text = re.sub(r'\s{2,}', ' ', text)
        # 7. Remove leftover brackets and parenthesis
        text = re.sub(r'[\[\]\(\)\{\}]', '', text)
        return text.strip()

    def _append_daily_memory(self, user_prompt: str, reply_text: str):
        """Appends each live dialogue turn to ~/memory/YYYY-MM-DD.md for the Muse daily log."""
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            daily_dir = Path.home() / "memory"
            daily_dir.mkdir(parents=True, exist_ok=True)
            daily_file = daily_dir / f"{today}.md"
            ts = datetime.now().strftime("%H:%M:%S")
            entry = f"\n### [{ts}] turn\n**User:** {user_prompt.strip()}\n**Kurek:** {reply_text.strip()}\n"
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"[Muse Memory] Error appending to daily memory: {e}")

    def _consolidate_with_jev(self, user_prompt: str):
        """Snap-judgment triage of single conversation turn using TypeSafe Jev."""
        try:
            import os
            from memory.config_manager import load_api_keys
            load_api_keys()
            if not os.environ.get("TYPESAFE_API_KEY"):
                return
            from typesafe_sdk import TypeSafeClient, Noul, Choice, Score
            client = TypeSafeClient()
            resp = client.system_one(
                state={"prompt": user_prompt},
                questions={
                    "is_durable": Noul(
                        instructions="Does `prompt` state a durable personal preference, identity detail, system rule, or boundary that should be remembered permanently?"
                    ),
                    "kind": Choice(
                        instructions="What category is this information?",
                        criteria={
                            "preference": "User likes, dislikes, habits, or technical preferences",
                            "boundary": "Strict operational rule, forbidden behavior, or hard constraint",
                            "fact": "Static technical, hardware, location, or project detail",
                            "transient": "Temporary note, short-term task, or conversational passing detail",
                            "other": "None of the above"
                        }
                    ),
                    "salience": Score(
                        instructions="How critical is this memory for future interactions?",
                        criteria=[
                            "Disposable note or temporary context",
                            "Useful contextual detail",
                            "Permanent core preference, strict boundary, or system constraint"
                        ]
                    )
                }
            )
            is_durable = resp.answers["is_durable"].noul > 0.70
            kind = resp.answers["kind"].choice
            salience = resp.answers["salience"].score
            conf = resp.answers["salience"].confidence

            if is_durable and salience >= 1.5 and kind in ("preference", "boundary", "fact"):
                today = datetime.now().strftime("%Y-%m-%d")
                citation = f"source:daily/{today}.md|jev:{salience:.1f}|conf:{conf:.2f}"
                line = f"- [{kind.upper()}] {user_prompt.strip()} <!-- [{citation}] -->\n"

                muse_core = Path.home() / "MEMORY.md"
                if muse_core.exists():
                    with open(muse_core, "a", encoding="utf-8") as f:
                        f.write(line)
                    print(f"[Muse Jev] 🧠 Consolidated durable {kind} into MEMORY.md: {user_prompt[:60]}...", flush=True)

                if kind == "boundary":
                    align_file = Path.home() / "ALIGNMENT_SYNTHESIS.md"
                    if align_file.exists():
                        with open(align_file, "a", encoding="utf-8") as f:
                            f.write(f"- **[BOUNDARY]** {user_prompt.strip()}\n")
                        print(f"[Muse Jev] 🛡️ Hoisted boundary into ALIGNMENT_SYNTHESIS.md", flush=True)
        except Exception as e:
            print(f"[Muse Jev Consolidation Notice] {e}", flush=True)

    def _check_auto_screen_context(self, user_prompt: str) -> str | None:
        """Uses TypeSafe Jev to detect if prompt requires on-screen / terminal visual context."""
        try:
            import os
            from memory.config_manager import load_api_keys
            load_api_keys()
            if not os.environ.get("TYPESAFE_API_KEY"):
                return None
            from typesafe_sdk import TypeSafeClient, Noul
            client = TypeSafeClient()
            resp = client.system_one(
                state={"prompt": user_prompt},
                questions={
                    "needs_screen": Noul(
                        instructions="Does `prompt` ask about visual UI layout, a compiler error, terminal output, a bug on screen, what is visible, or code currently in view?"
                    )
                }
            )
            prob = resp.answers["needs_screen"].noul
            if prob > 0.75:
                print(f"[Kurek Jev Vision] 👁️ Auto screen context triggered (P={prob:.2f}). Capturing active window...", flush=True)
                from actions.screen_vision import capture_screen_jpeg, query_vision, get_hyprland_context
                hypr = get_hyprland_context()
                win_title = hypr.get("title", "Unknown")
                win_class = hypr.get("class", "Unknown")
                jpeg_bytes, _ = capture_screen_jpeg()
                vision_summary = query_vision(jpeg_bytes, f"Identify any relevant code, error, or UI context in this window relating to: {user_prompt}")
                return f"[AUTOMATIC LIVE SCREEN CONTEXT]\nActive Window: '{win_title}' (App: {win_class})\nVision Analysis:\n{vision_summary}"
        except Exception as e:
            print(f"[Kurek Jev Vision Notice] {e}", flush=True)
        return None

    def _hourly_consolidation_loop(self):
        """Hourly background sweep checking for unindexed signals and nightly dream reflection."""
        while True:
            time.sleep(3600)
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                daily_file = Path.home() / "memory" / f"{today}.md"
                if daily_file.exists():
                    print(f"[Muse Jev] 🔄 Running hourly memory consolidation sweep on {daily_file.name}...", flush=True)

                # Nightly dream reflection (runs once per day around 05:00-06:00 CET)
                current_hour = datetime.now().hour
                if 5 <= current_hour <= 7:
                    from core.dream_cycle import execute_dream_cycle
                    d_res = execute_dream_cycle(force=False)
                    if d_res.get("status") == "success":
                        print(f"[Muse Dream] 🌙 Nightly dream journal created: {d_res.get('dream_file')}", flush=True)
            except Exception as e:
                print(f"[Muse Jev] Hourly sweep notice: {e}", flush=True)



class KurekHTTPHandler(BaseHTTPRequestHandler):
    engine: KurekEngine = None

    def log_message(self, format, *args):
        # Suppress routine polling logs
        pass

    def do_GET(self):
        if self.path == "/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            payload = json.dumps({"state": self.engine.state})
            self.wfile.write(payload.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/toggle":
            self.engine.toggle()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            payload = json.dumps({"ok": True, "state": self.engine.state})
            self.wfile.write(payload.encode("utf-8"))
        elif self.path == "/prompt":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body) if body else {}
            prompt_text = data.get("prompt", "")
            threading.Thread(target=self.engine.handle_text_query, args=(prompt_text,), daemon=True).start()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"processing"}')
        else:
            self.send_response(404)
            self.end_headers()


def run_server(engine: KurekEngine, port: int = 8790):
    KurekHTTPHandler.engine = engine
    server = ThreadingHTTPServer(("127.0.0.1", port), KurekHTTPHandler)
    print(f"[Kurek Server] 🚀 Listening at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    print("=" * 60)
    print("⚡ KUREK DAEMON STARTING (Headless macOS Native Mode)")
    print("=" * 60)

    engine = KurekEngine()

    # Background auto-silence watchdog
    def silence_watchdog():
        while True:
            time.sleep(0.1)
            if engine.is_recording:
                now = time.time()
                # 1. If speech was detected and followed by silence (1.2s) -> auto submit
                if engine.has_speech and (now - engine.last_sound_time > SILENCE_DURATION):
                    print(f"[Kurek] Silence detected after speech ({now - engine.last_sound_time:.2f}s) — auto-submitting…", flush=True)
                    engine.stop_listening_and_process()
                # 2. If no speech detected at all within 4.5s -> auto stop
                elif not engine.has_speech and (now - engine.record_start_time > 4.5):
                    print("[Kurek] No speech detected within 4.5s — auto-stopping listening…", flush=True)
                    engine.stop_listening_and_process()
                # 3. Safety cap: stop after MAX_RECORD_SECONDS
                elif engine.record_start_time > 0 and (now - engine.record_start_time > MAX_RECORD_SECONDS):
                    print("[Kurek] Max recording duration reached — auto-submitting…", flush=True)
                    engine.stop_listening_and_process()

    threading.Thread(target=silence_watchdog, daemon=True).start()

    run_server(engine)
