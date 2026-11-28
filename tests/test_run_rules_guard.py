"""The run tool must not let the model write the user's rules file (docs/ANTI-DRIFT.md)."""
from __future__ import annotations

from pathlib import Path

import pytest

from actions.run import run

RULES_TEXT = b'[[rule]]\nid = "form-of-address"\ntext = "Call me boss."\n'


@pytest.fixture
def rules_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    cfg = home / ".config"
    (cfg / "kurek").mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("KUREK_CODEBASE_ROOTS", str(ws))
    monkeypatch.delenv("KUREK_RULES_FILE", raising=False)
    # No user systemd bus in CI: run the shell directly.
    monkeypatch.setattr("actions.dev_agent._wrap_systemd_scope", lambda argv: argv)
    path = cfg / "kurek" / "rules.toml"
    path.write_bytes(RULES_TEXT)
    return path


def _refused_cmds(p: Path) -> list[str]:
    return [
        # the 2026-11-08 shape
        f"cp {p} {p}.bak && python3 - <<'EOF'\nimport pathlib\np = pathlib.Path.home()/'.config/kurek/rules.toml'\np.write_text('x')\nEOF",
        "cp ~/.config/kurek/rules.toml ~/.config/kurek/rules.toml.bak-1 && python3 - <<'EOF'\nEOF",
        "sed -i 's/a/b/' ~/.config/kurek/rules.toml",
        "printf x >> ~/.config/kurek/rules.toml",
        "echo x >> ~/.config/kurek/rules.toml",  # unrestricted mode refuses a leading echo first
        "cat ~/.config/kurek/rules.toml > /tmp/x",
        "mv /tmp/new ~/.config/kurek/rules.toml",
        "mv /tmp/new $XDG_CONFIG_HOME/kurek/rules.toml",
        "sed -i s/a/b/ rules.toml",
        f"cat {p} | tee {p}",
        f"cat {p}; rm {p}",
        f"cat {p} $(rm {p})",
        f"cat {p} `rm {p}`",
        f"cat {p}\nrm {p}",
        "cat 'rules.toml",  # unparseable: fail closed
    ]


@pytest.mark.parametrize("unrestricted", [True, False])
def test_rules_file_writes_are_refused(rules_file: Path, monkeypatch: pytest.MonkeyPatch, unrestricted: bool) -> None:
    if unrestricted:
        monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    else:
        monkeypatch.delenv("KUREK_REMOTE_ALLOW_EXEC", raising=False)
    for cmd in _refused_cmds(rules_file):
        res = run({"action": "exec", "cmd": cmd})
        assert res.startswith("Refused:"), (cmd, res)
        assert "rules file" in res or cmd.startswith("echo"), (cmd, res)
        assert rules_file.read_bytes() == RULES_TEXT, cmd
        assert not rules_file.with_name("rules.toml.bak").exists(), cmd


def test_read_only_commands_on_rules_file_run(rules_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    for cmd in (
        "cat ~/.config/kurek/rules.toml",
        "grep -n form-of-address ~/.config/kurek/rules.toml",
        "wc -l ~/.config/kurek/rules.toml | cat",
        "grep -nE 'form|zzz' $XDG_CONFIG_HOME/kurek/rules.toml",
    ):
        res = run({"action": "exec", "cmd": cmd})
        assert not res.startswith("Refused:"), (cmd, res)
        assert "Exit: 0" in res, (cmd, res)
    assert rules_file.read_bytes() == RULES_TEXT


def test_unrelated_command_unaffected(rules_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    res = run({"action": "exec", "cmd": "printf hi"})
    assert not res.startswith("Refused: the user's rules file")
    assert "Exit: 0" in res


def test_custom_rules_file_name_is_guarded(rules_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUREK_REMOTE_ALLOW_EXEC", "1")
    monkeypatch.setenv("KUREK_RULES_FILE", str(rules_file.with_name("mine.toml")))
    res = run({"action": "exec", "cmd": "sed -i s/a/b/ ~/.config/kurek/mine.toml"})
    assert res.startswith("Refused:") and "rules file" in res


def test_description_tells_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from actions.run import _SANDBOX_DESCRIPTION, _UNRESTRICTED_DESCRIPTION

    for desc in (_SANDBOX_DESCRIPTION, _UNRESTRICTED_DESCRIPTION):
        assert "rules.toml" in desc and "read-only" in desc
