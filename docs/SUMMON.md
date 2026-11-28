# Summoning Kurek

| Trigger | How it works |
|---|---|
| **Middle-click** | `mouse:274` runs `kurek-trigger toggle`, which talks to `$XDG_RUNTIME_DIR/kurek.sock`. The first click starts listening, the second stops and sends. |
| **Typed prompt** | `SUPER + SHIFT + K` opens `kurek-ask` in a small floating terminal. Use it for names the microphone gets wrong. An empty line closes it. |
| **Launcher** | Open the launcher, type `Kurek`, press Enter. The desktop entry runs `kurek toggle`. |
| **CLI** | `kurek [toggle\|start\|stop\|status\|prompt <text>\|logs [-f]\|doctor]` |
| **Phone** | Kurek Remote sends prompts over Tailscale. See [REMOTE.md](REMOTE.md). |

## CLI commands

| Command | What it does |
|---|---|
| `kurek toggle` | Start or stop listening. This is the default. |
| `kurek start` / `kurek stop` | Start or stop the daemon, through the systemd unit when it is enabled. |
| `kurek status` | Print the daemon state. |
| `kurek prompt <text>` | Send a text prompt without the microphone. |
| `kurek logs [-f]` | Show recent logs. `-f` follows them. Uses journald when `kurek.service` is active. |
| `kurek doctor` | Check the daemon, the unit, the `.env` keys (presence only), the binaries, the microphone and the venv. |

## Listening

`KUREK_AUTO_SUBMIT=adaptive` is the default. Kurek stops on a pause after speech.

The pause is `KUREK_SILENCE_DURATION` (3.0 s) plus `KUREK_SILENCE_GROWTH` (0.3 s) for each second already spoken. The extra is capped by `KUREK_SILENCE_MAX_EXTRA` (2.0 s).

Set `KUREK_AUTO_SUBMIT=off` if you want only the second click to stop a recording. The second click always stops at once.

The full list of listening settings is in [REFERENCE.md](REFERENCE.md#configuration-env).
