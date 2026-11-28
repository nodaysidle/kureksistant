# Changelog

All notable changes to Kureksistant (Kurek) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project approximates [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- **No proposal for what memory already holds (`core/proposals.py`, `actions/memory_tool.py`):** `propose` refuses text that is an active ledger row (whitespace and case ignored, with or without its `key: ` prefix). `manage_memory` no longer proposes a boundary it has just saved to the ledger, which was the source of those duplicates.
- **No cut-off proposals (`core/proposals.py`):** entries were sliced to 300 characters, so a long sentence was stored as a fragment with nothing logged. The limit (`MAX_TEXT_CHARS`) is now 1500 characters, and text over it is refused with a logged warning that names the limit instead of being stored cut.
- **Dream cycle proposes only what the user said (`core/dream_cycle.py`):** every proposal must quote the user's own words from a User line of that day's log; the quote is checked in code and stored as `Heard (verbatim)`. Proposals with no quote, or a quote found only in Kurek's replies or nowhere, are dropped and the count is logged.
- **Jev triage on the Liquid server (`memory/config_manager.py`):** when `TYPESAFE_BASE_URL` points at `api.liquid.ai`, the client uses `LIQUID_API_KEY` and the model from `TYPESAFE_DEFAULT_MODEL` or `TYPESAFE_MODEL`. A TypeSafe key got 401 there and the SDK's default model 404, so memory proposals from speech were silently off.

## [0.2.6] - 2026-11-28

### Added
- **Camera watcher (`core/camera_watcher.py`):** watches a local RTSP camera for vehicles entering the region of interest, archives timestamped snapshots to the PC and Android, and sends alerts to Kurek Remote through the Android `CameraAlertService`.
- **Camera watcher precision:** fewer false positives, from class-agnostic NMS, geometry and exclusion-zone filters, parked-vehicle memory and directional entry checks.
- **Helium browser support (`actions/browser_control.py`):** Helium is detected as the default browser and has its own Chromium-engine entry and profile directory, so "open the browser" works when Helium is the default.
- **App aliases (`actions/open_app.py`):** `monstar`, `craft anything`, `cutcap`, `nodaysidle browser linux`.

### Changed
- **Default terminal, not a hardcoded one (`actions/open_app.py`, `actions/computer_settings.py`):** terminal programs (btop, htop, herdr) and the task manager open through `xdg-terminal-exec`, so they follow the system default terminal. The fallback list used only when `xdg-terminal-exec` is missing now starts with monstar instead of Ghostty; "open ghostty" still launches Ghostty itself.
- **Kurek reads only Hermes `USER.md` (`memory/reference.py`, `core/context.py`):** Hermes `MEMORY.md` (Hermes's own working memory and instructions) and the legacy `~/ALIGNMENT_SYNTHESIS.md` no longer reach the prompt or recall, and `docs/ANTI-DRIFT.md` and `docs/GLOSSARY.md` say so.
- **Website and file-manager aliases on Linux (`actions/open_app.py`):** netflix, instagram and tiktok open their URL in the default browser, and safari opens the default browser. explorer, file explorer and finder open the home folder with `xdg-open`, and "task manager" opens btop, instead of pointing at Firefox and Nautilus, which may not be installed.
- **CI runs on `ubuntu-24.04` (`.github/workflows/ci.yml`):** the PortAudio package on Ubuntu 26.04 has a PulseAudio backend that fails at import when no audio server runs, which stopped test collection on `ubuntu-latest`.
- **Knowledge vault tool reads the current vault (`actions/vault_knowledge.py`, `core/context.py`):** `vault_knowledge` now defaults to `~/dev/nodaysidle/nodaysidle-vault` instead of the archived `nodaysidle-knowledge`. The project overview is `wiki/hot.md` (then `wiki/index.md`), note names match case-insensitively ("cascade v3" finds `Cascade-V3`), and `read` and `search` only touch `wiki/` (minus `wiki/meta`) and `inbox/`, never `plans/`, `graft/`, `tools/` or hidden folders. The X / social-media promo notes are gone from the tool text and the system prompt block, since the new vault does not have them.
- **Vault captures commit themselves (`actions/vault_knowledge.py`):** `add_inbox` commits only the new `inbox/` file (path-limited, hooks on) when the vault is a git repo, so the vault no longer reports an uncommitted capture at the end of every session. The reply says whether the commit succeeded; if it fails or times out, the file stays on disk and the reply says it is uncommitted.

### Fixed
- **"Open btop" and "open htop" (`actions/open_app.py`):** they were started without a terminal, so nothing appeared while Kurek reported success. A terminal program that is not installed now reports failure.
- **Aliases with arguments (`actions/open_app.py`):** an alias such as `cutcap open` or `libreoffice --writer` is launched as a command with arguments instead of being looked up as one program name.
- **Hyprland window commands on a Lua-config Hyprland (`actions/open_app.py`, `actions/computer_control.py`, `actions/computer_settings.py`):** old-style `hyprctl dispatch` calls fail to parse on Hyprland with a Lua config but still exit 0, so focus, workspace and close commands reported success and did nothing. Dispatches now use the Lua form first, fall back to the old form, and report a failed dispatch.
- **Tests on a machine that exports its live settings (`tests/conftest.py`, `tests/test_installer_templating.py`):** `KUREK_*` variables and API keys are removed before test modules import the daemon, so import-time constants such as the recording ceiling use their defaults. The installer test shadows a system-wide `uv` with a stub instead of dropping its `PATH` directory.

### Security
- **The shell tool refuses to change the rules file (`actions/run.py`):** a command that names `rules.toml` runs only when it is plainly read-only (`cat`, `grep`, `head` and similar, no redirection, heredoc or substitution). The file tool already refused it; the shell tool did not, and an indirect write through a script or a glob is not caught.

## [0.2.5] - 2026-11-22

### Changed
- **Kurek Remote 0.2.5 (Android):** light redesign, same behaviour. Light theme with dark system-bar icons, system sans type (monospace only for the host, tool names and timestamps), vector icons instead of any emoji, message bubbles capped at 82% of the width, an input bar with two equal round buttons, and a Terminal chip that moved from the header into the preset row so the status line fits. Settings, the 2x1 widget and the live-call notification follow the same look. The app asks for no new permissions and keeps your saved host, port and token.
- **Read-only tool rounds run in parallel:** when every tool call in one model round is a read-only lookup (web search, weather, memory recall, vault list/read/search, codebase search/tree/read/find/outline), they run at the same time; results reach the model in the original order. Any other mix runs one by one, as before.
- **Reused HTTPS connections** for speech-to-text uploads and DeepSeek web search. A connection the server had closed is resent once at once.
- **DeepSeek stream retry:** a dropped connection is retried at once; HTTP 429/5xx still wait 1 s.

### Added
- **`KUREK_TIMING=1` covers the whole turn:** voice turns start at the stop click, so speech-to-text is measured; new marks for the DeepSeek request, headers and first reasoning token, each tool, the first sentence's speech synthesis, and mpv start. The speech marks now print (they ran on a thread without a clock).
- A `[Kurek Summary Round]` log line each time the extra answer round runs after tools.

### Fixed
- **`system_monitor` status says memory completely:** it now reports usable RAM, available RAM and the installed RAM, and lists zram (compressed, in RAM) and disk swap separately with a closing "Swap is not RAM" sentence, so the model no longer runs `free` and a combined swap total is never read as "29 GB of RAM".
- **Kurek Remote dictation no longer sends by itself (behaviour change, on purpose):** the mic button now puts what you say into the prompt field, live as you speak, and you press Send. It waits about 6 seconds of silence before it stops listening (some phones ignore this hint), keeps what you already typed, and says "Did not catch that" instead of failing silently.
- **Kurek Remote keeps a half-streamed reply:** when the connection drops or the PC ends a turn with an error after some of the answer arrived, that text stays and the error appears as its own row below it; audio already queued keeps playing.
- **Kurek Remote no longer waits forever:** a reply stream that closes without `done` or `error` now shows an error. The daemon's "Still finishing the previous request" shows as Busy, not Offline. After an error the status is re-checked at 2, 5 and 15 seconds; a prompt is never resent by itself.
- **Files land where you can find them:** a bare or relative file name (`notes.md`) is saved in `~/Downloads/KUREK/NOTES` (`KUREK_NOTES_DIR` overrides) instead of the daemon's working directory, the repo. Create and write results give the absolute path and say when an existing file was replaced and how long it was; `file_controller` exposes `append`.
- **Vault inbox:** a second capture with the same title on the same day gets `-2`, `-3` instead of replacing the first.
- **Vault search treats the query as text:** a query that starts with `-` is searched for, never read as a ripgrep option.
- **A brain failure after tools ran** says what the tools did and keeps the turn in history, then names the failure.
- **Phone streams always end:** a brain failure, an empty answer or a cancelled turn sends `error` and `done`, so Kurek Remote no longer waits.
- **Cancelled turns** keep the tools they already ran in history, and no further tool of that turn starts. Nothing is said aloud, as before.
- **Unreadable tool arguments** (invalid JSON) are no longer run as empty arguments; the model is told and can retry.
- **Typed box and CLI:** `kurek-ask` prints one line when a prompt is not sent; `kurek prompt` reports a refused connection (`curl --fail --max-time 5`).
- **Learned memory:** replacing an entry that was already replaced or forgotten is refused, as forgetting it twice already was.
- **Browser search** URL-encodes the query (`c++`, `&`, `?`).
- **user_data** says a field is not in memory instead of inventing a random value.
- **A bare "close it"** no longer sends Ctrl+W: Kurek asks which window, tab or app you mean. "close this" and "close tab" still close the current tab.
- **Live voice after a late Tailscale:** when Tailscale comes up after the daemon started, live voice (port 8791) binds the Tailscale IP too, as HTTP already did.
- **Phone streams survive long tools:** during a phone turn the daemon sends an SSE comment every 15 s, so Kurek Remote's 90 s read timeout no longer fires during a slow tool; the app ignores those lines.
- **Idle HTTP connections close after 30 s:** a request that stops arriving for 30 s no longer holds a daemon thread. A phone stream ends only when the phone accepts nothing for 120 s: replies go out in 16 KB slices, so a large audio event on a slow link is not cut.
- **Long replies are never cut by a timer:** Kurek waits as long as speech keeps playing; only your click, a new prompt, or 3 minutes with no progress at all stop it. Kurek's mpv loads no system-wide scripts, so media keys (mpv-mpris) cannot pause its speech.
- **Background messages wait their turn:** resource alerts, finished-job announcements and screen-watch comments are said after a reply or recording ends, never over it; after 5 minutes without a quiet moment they become a notification.
- **A cut answer says so:** when DeepSeek ends an answer early (the connection dropped mid-answer, the length limit, or it ran short of resources), Kurek adds one sentence saying the answer is incomplete, on the PC and on the phone. The conversation history keeps the answer without that sentence, so the model does not repeat it on later turns.
- **The phone gets the whole reply:** `done` is sent only after every sentence's audio, and a phone that freezes for up to 120 s still receives the rest. A prompt on the PC ends a phone turn stuck on an unresponsive phone at once instead of waiting for it.
- **Recording keeps every word:** "Speak now" appears only once the microphone is running, the last words before your stop click are kept, a double click no longer opens two microphones, and a phone prompt no longer ends your recording. A microphone that fails to start is shown as a notification.
- **Nothing you said vanishes:** a new prompt waits up to 150 s for a running tool; if it still cannot run, Kurek says so aloud (unless you are recording) and the notification shows your words. A waiting prompt is not answered once you click, send a newer one or start recording; the notification shows its words. A click that stops your recording also silences Kurek at once. Typed prompts up to 64 KB arrive whole; longer ones are refused with a message.
- **Inline code is read out** without its backticks; a copied "Interrupted before replying." is dropped and the rest of the answer is spoken; a sentence no cloud voice could speak is shown as a notification.

### Security
- **File tools refuse secret files:** `file_controller` read and `file_processor` refuse `.env*` (not `.env.example`), `api_keys.json`, `*.pem`/`*.key`, `id_*` keys, `.netrc`, `.git/*` and anything in `~/.ssh`, also through a symlink or a bare name in the notes folder. Nothing from them reaches the model or Gemini. Shell dotfiles, `~/.config/kurek` and other config stay readable.
- **Tool lines are redacted:** the journal's `[Kurek Tool Dispatch]` / `[Kurek Tool Result]` lines and the phone's `tool` event pass through the `run` tool's secret patterns and are cut (500 / 100 characters). The model still gets the full result.
- **Clipboard history skips password-manager clips:** a clip a password manager marks (`x-kde-passwordManagerHint`, as `wl-copy --sensitive` does) is never read or saved. Other clips are kept as copied, so a pinned key can still be copied back. The watcher starts with the daemon, no longer when the tool is imported, so a test run no longer touches the real history.

## [0.2.4] - 2026-11-18

### Added
- **Recall over past conversations:** `manage_memory` recall searches the daily logs with several keywords and answers with the date of what was said. Nothing recall finds becomes memory unless you ask.
- **Who wins:** the prompt ranks rules, your current message, learned memory, `~/MEMORY.md` and Hermes, then earlier chat. Before going against a lasting fact (terminal, city, where files go) Kurek asks whether it changed.
- **Project-aware file tasks:** projects are the git repos in `~/dev/nodaysidle`. Kurek looks first, asks one question with a grouped plan for files that clearly belong to a project, and leaves files it is unsure about alone.
- **One-sentence connections** to a project or past conversation when they change the answer.
- `GLOSSARY.md` names each memory store.
- **Claim gate (observe-only):** each spoken sentence is checked against what the tools did that turn. A first-person "deleted / sent / saved" that no tool outcome backs (no call, failed, timed out, or still waiting for confirmation) is logged to `~/.local/share/kurek/claim-violations.jsonl`. Nothing spoken changes. `KUREK_CLAIM_GATE=off` disables it. English only. See `core/claim_gate.py`.
- **Dictionary:** each transcript is corrected with the `replacements` in reverseduo's `~/.config/reverseduo/dictionary.json` ("no days idle" becomes "nodaysidle"), whichever speech engine produced it. Read-only; risky entries (command words, symbol words, short words, other machines' paths) are skipped. `KUREK_VOCAB_FILE` overrides the path, empty disables. See `core/vocabulary.py`.
- **Typed box:** `kurek-ask` (installed with the CLI) is a one-line prompt for names the microphone gets wrong, run in a floating terminal on `SUPER + SHIFT + K`. See the README.
- When the learned-memory block outgrows `KUREK_MEMORY_FILE_MAX_CHARS`, the prompt now says how many older entries are hidden and points to recall.

### Changed
- **English only.** Kurek replies in English whatever language a message is in (`english-only` rule in `rules.toml`, pinned). Recall no longer understands Slovenian or Italian time words. The `slovenian-allowed` eval case became `slovenian-gets-english`, and `italian-gets-english` was added.
- The confirmation prompt no longer mentions "the user's own language".
- Chat turns from before a memory correction made outside the chat are no longer sent to the model, so the old fact in the chat cannot win (a warning is logged).
- Ghostty is the default terminal again.
- `~/Projects` (legacy) dropped from `workstation_radar` and the codebase tool's default roots, and from the vault lookup.
- Recall ignores filler words ("what did I say about"), which matched nearly every past turn and buried the real hits.
- **XDG runtime storage (`memory/paths.py`):** moved live runtime data and state out of the repo `memory/` tree to `$XDG_DATA_HOME/kurek/` (`kurek_memory.db`, `clipboard_history.json`) and `$XDG_STATE_HOME/kurek/` (`kurek_history.json`), with explicit one-shot startup migration (`migrate_legacy_runtime_files`) and full XDG isolation in tests and `scripts/verify.sh`.
- **User profile facts (`actions/computer_control.py`):** `user_data` now sources identity fields from `memory.reference` and active `memory.ledger` entries instead of the retired `memory/long_term.json` file.
- **Single-purpose shell rules (`actions/run.py` & `actions/screen_vision.py`):** `run(action="exec")` rejects chained `cd ... && ...` (use `cwd`) and `echo ...` wrapper commands; deictic screen references prioritize `screen_vision` before hypothesis generation.
- **Internal:** the prompt is built in one place, `core/context.py` (`build_messages`), which also holds the who-wins ranking. Hermes and `~/MEMORY.md` are read by `memory/reference.py` for both the prompt and recall. The prompt the model sees is unchanged byte for byte.

### Fixed
- **Concurrent muted queries (`kurek_daemon.py`):** split `_engine_lock` (LLM/tools) and `_speaker_lock` (local TTS playback) so remote/CLI queries with `mute_speaker=True` execute without waiting for or interrupting active local desktop speech.
- **Operator naming consistency (`kurek_daemon.py`, `scripts/rules_eval.toml`):** rule 5 forbids apology deflections under both `Boris` and `Master Alan`.
- **Documentation & packaging:** reconciled `README.md` RAM footprint to `~450MB peak RAM (with faster-whisper loaded)` and bumped release metadata to `0.2.4`.

### Removed
- The Claude Haiku brain (`KUREK_LLM=haiku`, `anthropic` package). DeepSeek-Flash (V4.1) is the only brain.
- The old personal-context store and `~/memory/bank/` from the prompt and from recall.
- `memory/memory_manager.py` (the retired store's code; only its DB path was still used). The legacy `memory/long_term.json` import had already stopped running and is gone; the file is left on disk.

## [0.2.3] - 2026-10-28

### Changed
- **Reply length and language live only in `rules.toml`.** The system prompt now defers to `[USER RULES]` for both, with a short-reply, user's-language fallback when no such rule exists. `tests/test_anti_drift_prompts.py` enforces this and checks that nothing but `core/rules.py` touches the rules file.
- **rules_eval:** the `english-only` case is replaced by `slovenian-allowed` and `other-language-gets-english`.

### Fixed
- **Kurek Remote 0.2.3 (Android):** rotating the phone no longer restarts the screen, which wiped the conversation and lost a reply still on its way. The main screen now handles rotation itself (`android:configChanges`).

### Removed
- **`core/prompt.txt`:** no code loaded it, and keeping it in step with the real prompt was a source of drift.

### Documentation
- **Correction to the 0.2.0 entry below:** it lists "~0.8ms median trigger, 24 tools" as measured figures. The 0.2.0/0.2.1 tool count was **25** (25 `actions/*.py` files defined a `TOOL` dict); 0.2.2 ships **27**. The trigger timing is an informational, machine-dependent benchmark of `bin/kurek-trigger` against a mock UDS daemon (`scripts/bench_trigger.py`), not end-to-end latency, so it is no longer quoted as a number. The 0.2.0 entry is left unchanged as a historical record.
- AGENTS.md: removed the "~0.8ms median" figure and describes the benchmark as above.

## [0.2.2] - 2026-10-28

### Added
- **Live voice on the phone (opt-in, `KUREK_LIVE_VOICE=1`):** `core/live_voice.py` serves `ws://<host>:8791/live` on the same hosts as HTTP and bridges the phone's 16 kHz PCM mic audio to xAI `grok-voice-3.0` (voice Sol), streaming 24 kHz PCM back. The xAI key stays on the PC. The endpoint needs the remote Bearer token, even on loopback, and rejects browser `Origin` requests.
- **Read-only tools during live calls:** system status, weather, web search, workstation radar, codebase reads, the vault, memory recall and process list. The daemon enforces the same allowlist again before anything runs.
- **Kurek Remote 0.2.2 (Android):** a redesign in the Omarchy Miasma theme (direction in `android/DESIGN.md`), a Talk live button with live transcripts, a Terminal action that opens herdr in Foot on the PC, and a 2x1 home-screen widget. A foreground service owns the call, so it keeps running with the screen off, with an ongoing notification, a timer and an End action.
- **Typed decisions, Jev first:** `core/decision_client.py` asks `typesafe/jev-1.14` on OpenRouter Decisions first, then `microsoft/microsoft-decision-1`, then a local heuristic, for screen context, code intent and dev-agent error judgments.
- **DeepSeek Search API for `web_search`:** DuckDuckGo and Google News RSS are the fallback.
- **OpenRouter speech-to-text by default:** new `OpenRouterSTT` in `core/stt.py` using `openai/whisper-large-v3-turbo` (override with `OPENROUTER_STT_MODEL`). `KUREK_STT_PROVIDER` defaults to `openrouter`; without an OpenRouter key the daemon falls back to Deepgram Nova-2, then local faster-whisper.
- **Spoken summaries and fallback replies:** when tools run but the model emits no spoken text, the daemon asks the model for a spoken summary of 1 to 4 sentences. Final fallbacks: an error notice, the short tool output itself, or "Done."
- **Android speak-aloud option:** Kurek Remote has a speaker toggle (persisted setting) and an `AudioQueuePlayer`; the daemon can stream synthesized TTS audio (base64) with each sentence to the phone.
- **File tool aliases:** `file_controller` accepts shell-style action aliases (`ls`, `mkdir`, `rm`, `mv`, `cp`, `cat`, `touch`, `save`, `search`, …) and shortcut sub-paths such as `desktop/notes.txt`.
- **Optional Claude Haiku brain:** `KUREK_LLM=haiku` switches reasoning and tool dispatch to Claude Haiku 5.5 (`ANTHROPIC_API_KEY` or `HAIKU_API_KEY`; `KUREK_HAIKU_MODEL`, `KUREK_HAIKU_EFFORT`, `KUREK_HAIKU_THINKING`). DeepSeek-Flash stays the default. `scripts/llm_ab.py` compares the two on the same prompts and tool loop. The startup log, the startup key check and stream errors name the active brain.
- **User rules (`core/rules.py`):** `~/.config/kurek/rules.toml` is injected into every prompt as `[USER RULES]`, word for word. Only the user edits it; a malformed file injects a visible notice instead of failing the reply.
- **Append-only memory ledger (`memory/ledger.py`):** `manage_memory` and Jev triage write to a `ledger` table that refuses updates and deletes. Every row needs a source, and a correction supersedes the old row (same key, or `replaces=<#id>`). The prompt carries only entries nothing has replaced.
- **Rule proposals (`core/proposals.py`):** the dream cycle, Jev boundary triage and `manage_memory` append candidate rules to `~/kurek_proposed_rules.md` instead of writing to `ALIGNMENT_SYNTHESIS.md`. Nothing reads that file into a prompt; the user promotes an entry by copying it into `rules.toml`.
- **Truncation warning:** a memory file longer than `KUREK_MEMORY_FILE_MAX_CHARS` now logs a warning naming the file, its size and the cap, instead of being cut without a trace.
- **Pinned rules:** a rule with `pin = true` in `rules.toml` is also re-sent verbatim right after the user's message each turn (model-facing only, never stored in history). Models weigh the end of the context most; pinning the brevity rule cut replies from 3 sentences to 2 in the rule-following check.
- **Rule-following check (`scripts/rules_eval.py`) and `docs/ANTI-DRIFT.md`:** adversarial prompts run through the real prompt and tool loop, scored by plain code with rules on and rules off. The document states the invariants and the rules for changing memory code.
- **27 tool modules:** new `codebase` (read-only repo inspection) and `run` (sandboxed, audited command runner) tools, up from 25 in 0.2.1.
- **`kurek doctor` and `kurek logs [-f]`:** doctor checks API keys (presence only, never values), required binaries (mpv, wtype, playerctl, spd-say, …), mic, socket, service and `.env` permissions; logs follows journald or the fallback log.
- **Local speech-to-text fallback:** when cloud STT (OpenRouter/Deepgram) fails, the utterance is retried on local faster-whisper (`FallbackSTT`). `KUREK_WHISPER_MODEL` selects the local model (default `base.en`).
- **Startup readiness notification:** one notification lists missing keys, missing voice/player binaries or a failed speech model.
- **Installer flags:** `--no-enable`, `--dev`, `--locked`; `requirements-dev.txt` and a pinned `requirements.lock`.
- **Weather with real data:** `weather_report` returns current conditions and today's/tomorrow's forecast from wttr.in instead of opening a browser search.

### Changed
- **Longer spoken answers:** up to 6 sentences and 150 words for explanations and conversation; facts and action results stay short.
- **Tool rounds:** up to 15 tool rounds per request (was 5).
- **Streaming TTS queue:** sentences are synthesized by a worker thread and appended to mpv, so the DeepSeek stream no longer pauses for each xAI TTS round trip. Remote requests with `stream_audio` synthesize each sentence once (previously twice when the PC also spoke).
- **Wayland-native input:** typing, hotkeys and key presses use `wtype` on Wayland (pyautogui fallback); brightness uses `brightnessctl` only; wallpaper uses Omarchy's `omarchy-theme-bg-set`, then hyprpaper/swaybg; YouTube play/pause uses `playerctl`.
- **Installer enables the service:** `install_linux.sh` runs `systemctl --user enable --now kurek.service` and respects `XDG_CONFIG_HOME`.
- **`launch_kurek.sh`:** exports `.env` (so `KUREK_*` settings work outside systemd), logs to `~/.local/state/kurek/daemon.log` (mode 600, rotated at 5 MB) instead of `/tmp`, and refuses to start a second daemon while `kurek.service` is active.
- **State toasts:** only Listening and Thinking by default (`KUREK_STATE_NOTIFY=minimal|all|off`).
- **Prompt size cap:** each memory file injected into the system prompt is capped (`KUREK_MEMORY_FILE_MAX_CHARS`, default 6000; head + newest tail). Jev no longer appends duplicate memory/boundary lines.
- **Dream cycle:** reflects on *yesterday's* log, runs on the first hourly check after 05:00 (catching up if the PC was off), and preserves accumulated boundaries in `ALIGNMENT_SYNTHESIS.md` under "Persistent Boundaries".
- **Confirmation reply:** "Confirmed, doing it now." instead of "Confirmed and executed."; confirmation failures are logged and notified.
- **DeepSeek client:** default model fallback is `deepseek-flash` (served by DeepSeek-V4.1-Flash-1017; `deepseek-chat` is discontinued). One retry on connection errors / 429 / 5xx before any token; the stream connection is always closed.
- **CI:** installs `requirements-dev.txt`, runs `ruff check .` with the rules in `pyproject.toml` (E9, F63, F7, F82, F401, B006, B008), and shellchecks the CLI and launcher too.
- **Longer listen window:** 2.2 s of silence to auto-submit (was 1.2 s), 30 s maximum recording (was 12 s), 6 s initial-silence timeout (was 4.5 s). Tunable via `KUREK_SILENCE_DURATION`, `KUREK_MAX_RECORD_SECONDS`, `KUREK_INITIAL_SILENCE_TIMEOUT`.

### Fixed
- **Haiku brain after tool-heavy prompts:** the final round keeps the tool list and forbids calls with `tool_choice`, so Anthropic no longer rejects the replayed thinking blocks.
- **Phone stream:** `/remote/prompt` closes the connection after `done` instead of holding it open.
- **Tests:** a shell that exports the live `.env` no longer changes test results.
- **Dream cycle:** a failed LLM call no longer writes a placeholder dream that blocked retries for the day, and the nightly run no longer wipes boundaries added during the day.
- **Speech recognition errors:** network/auth failures no longer sound like "I didn't hear anything"; Kurek says recognition failed and notifies.
- **mpv chunk cleanup:** queued sentence clips of a long reply are no longer deleted after 30 s before they play.
- **History and memory writes** are atomic (temp file + rename) and private (mode 600): `kurek_history.json`, `long_term.json`, `api_keys.json`, `clipboard_history.json`, the SQLite memory db.
- **Hyprland watcher** re-resolves its socket after Hyprland restarts.
- **Honest tool results:** `send_message` no longer claims delivery; `computer_control` checks the focus command's exit code; `dream_tool` reports failures.
- **Timeouts** on one-shot subprocess calls in actions (grim, pip, notify, …) and on Gemini client calls (60 s).
- **mpv:** reap the audio sink instead of leaking an idle mpv per run.
- **PortAudio:** import PortAudio before numpy to avoid a daemon startup deadlock.

### Security
- **`rename_file`** rejects path separators and names that would leave `$HOME`.
- **Archive extraction** uses tar `filter="data"`, so crafted archives can't write outside the destination.
- **Gated unrestricted shell:** the `run` tool's unrestricted shell mode is OFF unless `KUREK_REMOTE_ALLOW_EXEC=1`; `KUREK_UNRESTRICTED_SHELL` / `KUREK_ALLOW_SHELL` alone no longer enable it. A loud warning is logged when enabled, and the tool description and prompts only mention arbitrary execution when it is on.

### Removed
- **Mouse-1 decision provider** and `MOUSE_API_KEY`; Gemini no longer classifies code intent.
- **Unsupported code:** the legacy PyQt6 GUI (`legacy/`), the macOS menu bar (`menubar/`), the unused Ollama/OpenAI-local LLM stack, Edge/Kokoro/ElevenLabs/Vosk speech engines, `core/wake_word.py`, `core/plugin_loader.py`, `core/audio_devices.py`, `core/installer.py`, the tool-less `actions/background_monitor.py`, `proactive.py`, `screen_processor.py`, and Windows-only branches in `actions/`.
- **Unused dependencies:** pytest moved to `requirements-dev.txt`; `beautifulsoup4` removed.
- **tkinter dialog:** `actions/youtube_video.py` no longer opens a tkinter URL dialog (Kurek is headless); it replies asking for the URL instead.

## [0.2.1] — 2026-10-13

Targeted architectural and stability refactor for Arch Linux / Omarchy Quattro (Hyprland, Ghostty, Herdr, Fish) and Android Remote client over Tailscale.

### Fixed
- **Zero-config `.env` installs:** Eliminated raw `config/api_keys.json` file reads across `actions/` modules (`dev_agent`, `code_helper`, `flight_finder`, `desktop`, `file_processor`, `computer_settings`, `computer_control`, `youtube_video`). All key access routes through `memory.config_manager.get_gemini_key()`, checking environment variables first.
- **Hyprland window controls:** Replaced X11 `pyautogui` hotkeys in `actions/computer_settings.py` with native `hyprctl dispatch` calls (`cyclenext`, `killactive`, `fullscreen 0`, `movewindow l/r`, `dpms off`), native Wayland screenshot capture via `grim`, and screen locking via `hyprlock`/`swaylock`.
- **Systemd 600M envelope safety:** Protected `kurek.service` against OOM killer by passing low-memory Chromium flags in `browser_control.py` and delegating heavy builds in `dev_agent.py` to `systemd-run --user --scope`. Added 120s timeout guard in `ActionRegistry.run`.
- **Private lock decoupling:** Removed private `_lock` import in `actions/background_monitor.py`, replacing with thread-safe public state persistence helpers.

### Added
- **Omarchy terminal & shell support:** Added first-class prioritization for `ghostty` and `herdr` in `_LINUX_TERMINAL_FALLBACKS` and `_APP_ALIASES`, and added `fish` shell execution support. Task manager launches `btop`/`htop` directly inside `ghostty` or `herdr`.
- **SQLite WAL + FTS5 memory backend:** Upgraded `memory/memory_manager.py` from full-file JSON rewrites to SQLite in WAL mode with FTS5 full-text search indexing (`entries_fts`), automatic migration of legacy `long_term.json`, and 100% backward-compatible API.
- **Remote prompt SSE optimization:** Muted local PipeWire workstation TTS during remote phone queries over Tailscale `/remote/prompt` unless `speak_local` is requested.
- **Conversational confirmation gate:** Wired `core/confirm.py` with `notify-send` and conversational affirmative/negative turn detection in `kurek_daemon.py` to confirm or abort pending destructive operations.

### Changed
- **LLM prompt alignment:** Synchronized `core/prompt.txt` with actual runtime tool schemas (`screen_vision`, `manage_memory`, `system_monitor`, `vault_knowledge`, `workstation_radar`, `process_sentinel`), removing legacy PyQt6 HUD instructions and defunct tool names.

## [0.2.0] — 2026-10-10

Hardening and measurement release on top of the v0.1.0 headless daemon:
audit fixes for UDS/streaming, portable install + socket security, lazy
Hyprland geometry, timing/bench tooling, and docs that claim only measured figures.

### Fixed

#### Stability (since v0.1.0 / Oct 8)
- Resolve daemon unbound-local error paths, dream-cycle return handling, and F821 undefined symbols; align dependencies ([`d863922`](https://github.com/nodaysidle/kureksistant/commit/d863922)).
- Canonicalize the headless daemon path; quarantine the unsupported PyQt6 GUI under `legacy/`; add CI smoke tests, `SECURITY.md` / `NOTICE`, and packaging deny-list ([#2](https://github.com/nodaysidle/kureksistant/pull/2)).
- Correct documented RAM footprint and tool count (24) ([#3](https://github.com/nodaysidle/kureksistant/pull/3)).

#### Audit PR #4 — stream/UDS correctness
- Keep UDS state-event subscribers open until disconnect; prune dead peers on broadcast failure (Waybar/AGS no longer see silent sockets).
- Raise systemd memory envelope for Whisper footprint; later refined in #6.
- Normalise dream-cycle LLM responses (`str` / `dict` / `None`) via shared helper.
- Catch DeepSeek stream/network errors, always reset daemon state to IDLE.
- Stop reporting failures as success (“All set.”); handle `error` / empty stream honestly without fake history.

#### Audit PR #5 — install paths & socket security
- Template `desktop/kurek.service` with `@KUREK_DIR@`; installer renders into `~/.config/systemd/user/` and writes `$XDG_CONFIG_HOME/kurek/install_path`.
- C trigger reads `install_path` for launcher fallback; polls the UDS socket instead of a fixed 250ms sleep.
- Idempotent `install_linux.sh`: pacman dep checks (mpv, grim, wl-clipboard, libnotify, gcc), hard-fail if gcc missing, always refresh venv + Playwright Chromium, install `~/.local/bin/kurek-trigger`.
- UDS: mode `0600`, `SO_PEERCRED` same-uid gate, no `/tmp/kurek.sock` fallback (private `~/.cache/kurek/run` when `XDG_RUNTIME_DIR` unset).
- C trigger: JSON escaping, 500ms send/recv timeouts, clean `-Wall -Wextra` build.

#### Audit PR #6 — Hyprland / timing / docs
- Parse Hyprland `.socket2` focus/open/move/workspace events into RAM; **no** `hyprctl` on every focus change.
- Lazy geometry: one seed `hyprctl` at startup + one fetch per vision/screen-capture request.
- `KUREK_TIMING=1` per-query ms marks (trigger receipt, first DeepSeek token/sentence, first `speak_chunk`, mpv `loadfile`).
- `scripts/bench_trigger.py` (mock UDS daemon); CI prints median/p95 without gating.
- Sentence splitter: flush short trailing text; do not split on `Dr./Mr./Mrs./Ms./e.g./i.e./etc./vs./St.` or decimals.
- `MemoryHigh=512M` / `MemoryMax=600M` (observed peak ~452MiB with faster-whisper).
- Docs claim only measured figures: ~450MB peak RAM, ~0.8ms median trigger, 24 tools.

### Added
- Streaming sentence-buffered DeepSeek TTS pipeline, UDS trigger client (`bin/kurek-trigger`), persistent PipeWire mpv sink, and Hyprland `.socket2` watcher (pre-audit feature land ahead of #4–#6).

### Changed
- Default packaging `VERSION` and CI dry-run packaging bump to **0.2.0**.

## [0.1.0] — 2026-10-06

Initial public release: headless Linux/macOS daemon, Middle-Click summon, DeepSeek-Flash + xAI Grok (Sol) voice, Muse Memory / TypeSafe Jev, 24 tool modules, portable installer, and CC BY-NC 4.0 licensing (derived from FatihMakes' JARVIS).

[0.2.1]: https://github.com/nodaysidle/kureksistant/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/nodaysidle/kureksistant/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nodaysidle/kureksistant/releases/tag/v0.1.0

