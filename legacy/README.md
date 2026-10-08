# Legacy PyQt6 JARVIS GUI (unsupported)

This folder contains the original monolithic desktop client that Kureksistant was derived from:

- `main.py` / `ui.py` — PyQt6 Gemini Live UI (“MARK LIII / JARVIS”)
- `dashboard/` — phone remote-control FastAPI dashboard
- `plugins/` — drop-in GUI plugins
- `requirements-gui.txt` / `setup_gui.py` — GUI-only installer path
- `jarvis.ico` — legacy Windows icon asset

**This path is unsupported.** New installs and development target the headless daemon:

```bash
cp .env.example .env   # add DEEPSEEK_API_KEY and XAI_API_KEY
./install_linux.sh
kurek start
curl -s http://127.0.0.1:8790/status
```

The files here remain for archaeology and optional local experiments only. Paths, brands, and dependencies may be stale; do not expect `python legacy/main.py` to work without manual wiring.
