"""
actions/_safe_paths.py — where a tool is allowed to write.

Leading underscore: core/action_loader.py discovers tools by module name, so
this file is a helper, not a tool the model can call.

The model picks paths. Left alone it can rewrite ~/.bashrc, drop a key into
~/.ssh, or edit rules.toml — the one file the anti-drift contract says only the
user writes. Everything here answers "may this path be written at all, and does
it need a human yes first?".
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

# Only the user edits these. is_rules_file() wins over every other check.
_RULES_FILE = Path(".config") / "kurek" / "rules.toml"

# Directories whose whole subtree is a credential store or boot/config surface.
_SENSITIVE_DIRS = (
    ".ssh",
    ".gnupg",
    ".password-store",
    ".local/share/keyrings",
    ".config/systemd",
    ".local/bin",
    ".config/kurek",
)

# Dotfiles a shell or CLI reads on every start; rewriting one is code execution.
_SENSITIVE_NAMES = frozenset({
    ".env", ".bashrc", ".bash_profile", ".zshrc", ".profile", ".netrc",
})

_SENSITIVE_GLOBS = ("*.pem", "*.key", "id_rsa*", "id_ed25519*")


def resolve(path_str: str | Path) -> Path:
    """Expand ~ and make the path absolute. Follows symlinks, so a link inside
    the home directory that points at ~/.ssh cannot smuggle a write past the
    checks below."""
    return Path(path_str).expanduser().resolve()


def is_within_home(p: Path) -> bool:
    try:
        return resolve(p).is_relative_to(resolve(Path.home()))
    except Exception:
        return False


def is_rules_file(p: Path) -> bool:
    try:
        return resolve(p) == resolve(Path.home() / _RULES_FILE)
    except Exception:
        return False


def is_sensitive(p: Path) -> bool:
    home = resolve(Path.home())
    rp = resolve(p)

    def _inside(base: Path) -> bool:
        try:
            return rp == base or rp.is_relative_to(base)
        except Exception:
            return False

    if any(_inside(home / d) for d in _SENSITIVE_DIRS):
        return True

    if _inside(home / ".config" / "fish" / "config.fish"):
        return True

    name = rp.name
    if name in _SENSITIVE_NAMES:
        return True
    if any(fnmatch.fnmatch(name, pat) for pat in _SENSITIVE_GLOBS):
        return True

    # A repo .env is local secrets wherever the repo is checked out.
    repo = resolve(Path(__file__).parents[1])
    return name == ".env" and _inside(repo)
