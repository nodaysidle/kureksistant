# Kureksistant Reference

Detailed configuration, filesystem layout, security model, troubleshooting, and stack reference. For the overview and quick install, see [README.md](../README.md).

## Configuration (`.env`)

Settings live in `.env` inside the install directory (loaded by `kurek.service` via `EnvironmentFile`). See [`.env.example`](../.env.example) for the complete annotated list.

| Variable | Needed | Purpose |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | Reasoning and tool dispatch (DeepSeek-Flash via `api.deepseek.com`). |
| `XAI_API_KEY` | Yes | Speech synthesis (xAI Grok TTS, voice Sol). Without it, speech falls through the TTS chain. |
| `GEMINI_API_KEY` | Recommended | Screen vision and Gemini-backed tools. `kurek doctor` reports it missing as a failure. |
| `OPENROUTER_API_KEY` | Optional | Primary STT (voice audio sent to OpenRouter Whisper) and typed decisions (`typesafe/jev-1.14`, then `microsoft/microsoft-decision-1`). |
| `DEEPGRAM_API_KEY` | Optional | Deepgram Nova-2 STT (voice audio sent to Deepgram). |
| `KUREK_STT_PROVIDER`, `KUREK_STT_LANGUAGE`, `KUREK_WHISPER_MODEL` | Optional | Cloud STT preference (`openrouter` default, or `deepgram`), pinned language, and local `faster-whisper` model (`base.en` default; `base` or `small` for other languages). |
| `KUREK_SILENCE_DURATION`, `KUREK_SILENCE_GROWTH`, `KUREK_SILENCE_MAX_EXTRA` | Optional | Adaptive pause window: 3.0 s base + 0.3 s per second spoken, up to 2.0 s extra. |
| `KUREK_AUTO_SUBMIT`, `KUREK_INITIAL_SILENCE_TIMEOUT`, `KUREK_MAX_RECORD_SECONDS` | Optional | Set `KUREK_AUTO_SUBMIT=off` for click-only recording; 30 s initial silence timeout; 180 s hard cap. |
| `KUREK_STATE_NOTIFY` | Optional | Desktop state toasts: `minimal` (default: Listening + Thinking), `all`, or `off`. |
| `KUREK_NOTES_DIR` | Optional | Destination for bare-name note saves (default `~/Downloads/KUREK/NOTES`). |
| `KUREK_USER_NAME`, `INPUT_DEVICE` | Optional | How Kurek addresses you; microphone name substring. |
| `KUREK_AUTO_SCREEN` | Optional | Set `1` to run a screen-context decision before every answer (off by default). |
| `KUREK_REMOTE_TOKEN`, `KUREK_REMOTE_BIND` | For phone | 32+ character Bearer token (`openssl rand -hex 32`) and `tailscale`. |
| `KUREK_REMOTE_ALLOW_EXEC`, `KUREK_REMOTE_LOCAL_TTS`, `KUREK_LIVE_VOICE` | Optional | Remote exec/unrestricted shell, speak remote replies on PC, and live voice WebSocket (`8791`). |

`.env.example` also documents dictionary corrections (`KUREK_VOCAB_FILE`, read from reverseduo's `dictionary.json`), the observe-only claim gate (`KUREK_CLAIM_GATE`), project roots (`KUREK_CODEBASE_ROOTS`), Hermes continuity (`HERMES_PROFILE`, `HERMES_MEMORIES_DIR`), memory prompt cap (`KUREK_MEMORY_FILE_MAX_CHARS`), and per-turn timing logs (`KUREK_TIMING=1`).

## Where things live

| What | Location |
|---|---|
| API keys and settings | `.env` in the install directory |
| Standing rules | `~/.config/kurek/rules.toml` (`KUREK_RULES_FILE` overrides) |
| Learned memory | `~/.local/share/kurek/kurek_memory.db` (`$XDG_DATA_HOME/kurek/`) |
| Clipboard history | `~/.local/share/kurek/clipboard_history.json` |
| Claim-gate log | `~/.local/share/kurek/claim-violations.jsonl` |
| Recent chat history | `~/.local/state/kurek/kurek_history.json` (`$XDG_STATE_HOME/kurek/`) |
| Daemon log | journald (`kurek logs`), else `~/.local/state/kurek/daemon.log` |
| Daily logs | `~/memory/YYYY-MM-DD.md` |
| Proposed rules | `~/kurek_proposed_rules.md` |
| Bare-name notes | `~/Downloads/KUREK/NOTES` |
| Control socket | `$XDG_RUNTIME_DIR/kurek.sock` |
| HTTP / Live voice | `127.0.0.1:8790` (plus Tailscale IPv4 when enabled); live voice on `8791` |

## Security model

Full policy and vulnerability reporting: [`.github/SECURITY.md`](../.github/SECURITY.md).

| Area | Behaviour |
|---|---|
| **Bind** | HTTP binds `127.0.0.1:8790` by default. `KUREK_REMOTE_BIND=tailscale` adds only the Tailscale IPv4 address. `KUREK_BIND` sets an explicit host. |
| **Token** | Remote bind requires `KUREK_REMOTE_TOKEN` of 32+ characters; otherwise the daemon stays loopback-only. Remote POSTs require `Authorization: Bearer <token>` (constant-time compare). `GET /status` needs no token so the app can check reachability. |
| **Browser requests** | Requests with an `Origin` header are rejected on loopback, there is no CORS, and loopback POSTs must use `application/json`. |
| **Body and timeouts** | Request bodies cap at 64 KB. Stalled request uploads drop after 30 s. Phone SSE streams end only if the phone accepts nothing for 120 s. |
| **Local access** | Loopback HTTP is unauthenticated by design so local scripts and triggers can call `/toggle` and `/prompt`. |
| **Exec tools** | Remote prompts cannot call `desktop_control`, `code_helper`, `dev_agent`, `run`, or `codebase` unless `KUREK_REMOTE_ALLOW_EXEC=1`. |
| **Secret files** | `file_controller` reads and `file_processor` refuse `.env*` (except `.env.example`), `api_keys.json`, `*.pem`, `*.key`, `id_*` keys, `.netrc`, `.git/*`, and anything under `~/.ssh` (including symlinks). |
| **Redaction** | Tool output lines in the journal and on the phone pass through secret redaction patterns. |
| **Clipboard** | Entries marked sensitive by password managers are never read or saved. |
| **Release builds** | `scripts/package_release.sh` fails hard if `.env` or `config/api_keys.json` are present. |

**Unrestricted shell (`KUREK_REMOTE_ALLOW_EXEC`):** unset by default. Setting it to `1` (or `true`/`yes`/`on`) enables exec-capable tools for remote prompts and turns on the `run` tool's unrestricted shell for both local and remote prompts. The daemon logs a warning when active. Only enable it on a machine and tailnet you fully trust.

## Troubleshooting

```bash
kurek doctor      # check daemon, socket, service, .env keys (presence only), binaries, mic, Python
kurek logs -f     # follow journald or daemon.log
kurek status      # print daemon state
```

| Symptom | Likely cause |
|---|---|
| No answer at all | `DEEPSEEK_API_KEY` is missing or the network is down. The brain requires internet; only STT has a local fallback. |
| Robotic or silent voice | xAI failed (missing key or credits) and speech fell through to `spd-say`. On Gemini's free tier, long replies can drop sentences due to rate limits. |
| Daemon restarted mid-turn | `kurek.service` enforces `MemoryHigh=512M` and `MemoryMax=600M`. Crossing the cap triggers a systemd restart. |
| `TIMED OUT` from a tool | Tool ran past 120 s. Python cannot forcibly kill it, so it may finish in the background; Kurek does not retry it. |
| Phone shows Offline or Busy | Offline: wrong host, port, or token, or Tailscale is down. Busy: daemon is still finishing the previous turn. |
| Wrong token check | Returns `401` when tested from another Tailscale device. |
| Claimed action did not happen | The observe-only claim gate logs mismatches to `~/.local/share/kurek/claim-violations.jsonl`. |

## Stack and pipeline stages

| Stage | Engine and file | Fallback / failure behaviour |
|---|---|---|
| **Summon** | `bin/kurek-trigger` (C) over `$XDG_RUNTIME_DIR/kurek.sock` | `bin/kurek` starts the daemon if offline, then falls back to HTTP on `127.0.0.1:8790` |
| **Listen** | `sounddevice` capture with room-adaptive VAD (`kurek_daemon.py`) | Shows a desktop notification if the microphone cannot start |
| **Transcribe** | `core/stt.py`: OpenRouter Whisper, then Deepgram Nova-2, then local `faster-whisper` | Failed cloud STT retries that utterance locally on `faster-whisper` |
| **Think** | `core/llm_client.py`: DeepSeek-Flash via `api.deepseek.com` | One retry before output: immediate on dropped connection, 1 s backoff on 429/5xx |
| **Act** | `core/action_loader.py` (27 modules in `actions/`, 120 s timeout) | Reported as timed out, never as success, never retried |
| **Speak** | `core/tts.py` into persistent `mpv` sink (`core/mpv_sink.py`) on PipeWire | xAI Grok (Sol), then OpenRouter, then Gemini (Charon), then `spd-say` |
| **Checks** | `ruff`, `pytest`, `scripts/verify.sh` | Isolated verification that never touches the live database or port |
