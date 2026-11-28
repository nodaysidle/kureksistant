# What Kurek can do

Kurek has 28 tool modules in [`actions/`](../actions/). Each module declares a `TOOL` with a name and a description, and the model chooses among them. This page groups them by what they touch.

## Desktop

- Toggle fullscreen on the active Hyprland window.
- Adjust volume, brightness and Wi-Fi.
- Inspect the active window with a screenshot and Gemini vision.
- "Close tab" closes the tab. Only an explicit "close window" closes the window.
- Open applications and the default terminal, which is opened through `xdg-terminal-exec`.

## Files, clipboard and notes

- Read, organise and search files. Secret files such as `.env`, `*.pem` and `~/.ssh` are blocked.
- Undo restores a file's previous contents after a write.
- Manage clipboard history without touching password-manager secrets.
- Dictate drafts to `wl-copy`.
- Query a knowledge vault. Set `KUREK_VAULT_DIR` to the vault folder.

## Code and projects

- Read-only codebase inspection, and git status across your project roots (`KUREK_CODEBASE_ROOTS`).
- A sandboxed command runner (`run`). Remote prompts cannot use exec-capable tools unless `KUREK_REMOTE_ALLOW_EXEC=1`.
- Background build and test watchdogs (`process_sentinel`).

## Web and daily tools

- Web search, browser control, weather, reminders, YouTube and flight search.
- Browser control uses the Chromium-family or Firefox browser it detects on the system.

## Camera

- The optional RTSP camera watcher and on-demand camera checks. See [CAMERA.md](CAMERA.md).

## Confirmations

Destructive steps, such as deleting a file, pause for a plain yes or no, on the PC or from an authenticated phone.

## Speech timing

- Auto-submit is adaptive by default, and `KUREK_AUTO_SUBMIT=off` makes only the second click stop a recording. See [SUMMON.md](SUMMON.md).
- Kurek does not cut off a reply in the middle of speech.
- If the model's reply stops early because the connection dropped or it hit a length limit, Kurek says the answer is incomplete.
- A tool that does not finish within its time limit is reported as timed out. Kurek does not run it again and does not report success.

## Memory and rules

Kurek reads your rules, what you say now, learned memory, reference files and then recent chat, in that order.

"Fresh start" clears only the recent chat. Rules and memory are kept.

Memory stores and their rules are in [ANTI-DRIFT.md](ANTI-DRIFT.md).
