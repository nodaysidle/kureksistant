# Installing Kurek

This page has the detail behind the install steps in the [README](../README.md).

## What `install_linux.sh` does

The script is safe to run again. It never overwrites `.env`, `config/api_keys.json` or your memory stores.

1. Checks the Arch packages `mpv grim wl-clipboard libnotify gcc` with `pacman`.
   It prints the install command for anything missing and never runs `sudo` itself.
   On a non-Arch system it checks for the commands instead.
2. Stops if `gcc` is missing, because it builds `kurek-trigger`.
3. Creates `.venv`. With `uv` installed it runs `uv venv .venv --python 3.12`, otherwise `python3 -m venv`.
4. Installs `requirements.txt` into `.venv`, then runs `playwright install chromium`.
5. Writes the repository path to `$XDG_CONFIG_HOME/kurek/install_path` (default `~/.config/kurek/install_path`).
6. Copies `.env.example` to `.env` if `.env` does not exist.
7. Copies the icon to `~/.local/share/icons`.
8. Installs `kurek`, `kurek-ask` and `kurek-trigger` to `~/.local/bin`. The trigger is compiled with `gcc -O3 -Wall -Wextra`.
9. Renders `desktop/kurek.service` into `$XDG_CONFIG_HOME/systemd/user/kurek.service` and enables it with `systemctl --user enable --now`.
10. Installs a desktop entry in `~/.local/share/applications`.

## Flags

| Flag | Effect |
|---|---|
| `--no-enable` | Install the unit but do not enable or start it. |
| `--dev` | Also install `requirements-dev.txt` (pytest, ruff, mypy). |
| `--locked` | Install runtime dependencies from `requirements.lock` instead of `requirements.txt`. |

## The service unit

The unit runs `.venv/bin/python -u kurek_daemon.py` from the repository directory, reads `.env` if it exists, and sets `OPENBLAS_NUM_THREADS=1`.

- `Restart=on-failure` with `RestartSec=2`.
- `MemoryHigh=512M` and `MemoryMax=600M`. The comment in the unit records about 452 MiB observed at peak with faster-whisper loaded.
- It starts with `graphical-session.target` and after `pipewire.service`.

Useful commands:

```bash
systemctl --user status kurek.service
systemctl --user restart kurek.service    # after you change .env
journalctl --user -u kurek.service -n 50
kurek logs -f
```

## Keys

Edit `.env` after the installer runs. The minimum is `DEEPSEEK_API_KEY` and `XAI_API_KEY`. Every setting is listed in [REFERENCE.md](REFERENCE.md#configuration-env).

## Check the install

```bash
kurek doctor
```

`doctor` checks the daemon, the systemd unit, which keys are present (presence only, it never prints values), the binaries, the microphone and the virtual environment.

## Hyprland bindings

The bindings in the README call `kurek-trigger` and `kurek-ask` in `~/.local/bin`.

The typed prompt opens in a floating terminal through `xdg-terminal-exec`. The terminal it opens is the one set in `~/.config/xdg-terminals.list`. Kurek does not assume a particular terminal.

## Troubleshooting

- `kurek doctor` reports a missing key: add it to `.env`, then `systemctl --user restart kurek.service`.
- The unit did not enable: the installer prints the manual command. Run `systemctl --user enable --now kurek.service`, then `journalctl --user -u kurek.service -n 50`.
- No systemd user session: the installer skips the unit. Start Kurek with `kurek start`, which falls back to `launch_kurek.sh`.
- Middle-click does nothing: check that `~/.local/bin/kurek-trigger` exists and that the binding calls it.
