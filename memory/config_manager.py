import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def config_exists() -> bool:
    return CONFIG_FILE.exists()

def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["gemini_api_key"] = gemini_api_key.strip()

    CONFIG_FILE.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8"
    )

def _load_env_file() -> dict:
    import os
    env_vars = {}
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass
    return env_vars

def load_api_keys() -> dict:
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"❌ Failed to load api_keys.json: {e}")
    # Merge .env file and os.environ
    env_data = _load_env_file()
    import os
    for k, v in env_data.items():
        data[k] = v
        data[k.lower()] = v
        if k == "TYPESAFE_API_KEY" and k not in os.environ:
            os.environ[k] = v
    for k in [
        "DEEPSEEK_API_KEY",
        "DEEPGRAM_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "TYPESAFE_API_KEY",
        "KUREK_USER_NAME",
        "USER_DISPLAY_NAME",
        "HERMES_PROFILE",
        "HERMES_MEMORIES_DIR",
        "INPUT_DEVICE",
        "KUREK_PROJECT_DIR",
    ]:
        if k in os.environ and os.environ[k]:
            data[k] = os.environ[k]
            data[k.lower()] = os.environ[k]
    return data

def get_gemini_key() -> str | None:
    return load_api_keys().get("gemini_api_key")

def get_deepseek_key() -> str | None:
    import os
    keys = load_api_keys()
    return keys.get("DEEPSEEK_API_KEY") or keys.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")

def get_deepgram_key() -> str | None:
    import os
    keys = load_api_keys()
    return keys.get("DEEPGRAM_API_KEY") or keys.get("deepgram_api_key") or os.environ.get("DEEPGRAM_API_KEY")

def get_xai_key() -> str | None:
    import os
    keys = load_api_keys()
    return keys.get("XAI_API_KEY") or keys.get("xai_api_key") or os.environ.get("XAI_API_KEY")

def get_typesafe_key() -> str | None:
    import os
    keys = load_api_keys()
    return keys.get("TYPESAFE_API_KEY") or keys.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")

def is_configured() -> bool:
    return bool(get_deepseek_key() or get_gemini_key())

def get_wake_word() -> str:
    """Return wake phrase, defaulting to 'Hey Kurek'."""
    return load_api_keys().get("wake_word", "Hey Kurek")

def get_assistant_name() -> str:
    """Return configured assistant name, defaulting to 'Kurek'."""
    return load_api_keys().get("assistant_name", "Kurek") or "Kurek"


def get_user_name() -> str:
    """Return the configured user name for addressing (neutral default: empty)."""
    import os
    keys = load_api_keys()
    return (
        keys.get("KUREK_USER_NAME")
        or keys.get("USER_DISPLAY_NAME")
        or keys.get("user_name")
        or keys.get("user_display_name")
        or os.environ.get("KUREK_USER_NAME")
        or os.environ.get("USER_DISPLAY_NAME")
        or ""
    )


def get_hermes_profile() -> str:
    """Optional Hermes profile name under ~/.hermes/profiles/<name>/memories."""
    import os
    keys = load_api_keys()
    return (
        (keys.get("HERMES_PROFILE") or keys.get("hermes_profile")
         or os.environ.get("HERMES_PROFILE") or "")
    ).strip()


def get_hermes_memories_dir() -> str:
    """Optional absolute/tilde path override for Hermes memories directory."""
    import os
    keys = load_api_keys()
    return (
        (keys.get("HERMES_MEMORIES_DIR") or keys.get("hermes_memories_dir")
         or os.environ.get("HERMES_MEMORIES_DIR") or "")
    ).strip()


def resolve_hermes_memory_dirs() -> list[Path]:
    """Candidate Hermes memory directories, most specific first. No machine-specific defaults."""
    dirs: list[Path] = []
    explicit = get_hermes_memories_dir()
    if explicit:
        dirs.append(Path(explicit).expanduser())
    profile = get_hermes_profile()
    if profile:
        dirs.append(Path.home() / ".hermes" / "profiles" / profile / "memories")
    dirs.append(Path.home() / ".hermes" / "memories")
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["assistant_name"] = assistant_name.strip() or "Kurek"
    data["user_name"] = user_name.strip()
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["wake_word_enabled"] = bool(enabled)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data["morning_brief_enabled"] = enabled
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(fields)
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def get_input_device() -> str:
    """Microphone device name substring, or '' for the system default."""
    import os
    keys = load_api_keys()
    return (
        keys.get("INPUT_DEVICE")
        or keys.get("input_device")
        or os.environ.get("INPUT_DEVICE")
        or ""
    ).strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_openrouter_key() -> str:
    """Return configured OpenRouter API key or empty string."""
    return (load_api_keys().get("openrouter_api_key", "") or "").strip()


def save_openrouter_key(key: str) -> None:
    _patch_config(openrouter_api_key=(key or "").strip())


def get_openrouter_model() -> str:
    """Return preferred OpenRouter model (default deepseek/deepseek-chat)."""
    return (load_api_keys().get("openrouter_model", "") or "deepseek/deepseek-chat").strip()


def save_openrouter_model(model: str) -> None:
    _patch_config(openrouter_model=(model or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# printer control/watchdog/autoeject trio) can share ONE namespace.
def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    return dict(val) if isinstance(val, dict) else {}


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    cur.update(values)
    pc[namespace] = cur
    data["plugin_config"] = pc
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")


def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    CONFIG_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
