"""go_to without a url must fail loudly instead of opening a blank browser."""
import actions.browser_control as bc


def test_go_to_without_url_is_an_error_and_opens_nothing(monkeypatch) -> None:
    opened: list = []
    monkeypatch.setattr(bc, "_open_native", lambda url, browser: opened.append(url) or "Opened")
    result = bc.browser_control({"action": "go_to", "browser": "firefox"})
    assert result.startswith("Error: go_to needs a url")
    assert opened == []


def test_go_to_with_url_still_opens(monkeypatch) -> None:
    opened: list = []
    monkeypatch.setattr(bc, "_open_native", lambda url, browser: opened.append(url) or f"Opened {url}")
    bc.browser_control({"action": "go_to", "browser": "firefox", "url": "https://github.com"})
    assert opened == ["https://github.com"]


def test_brave_origin_is_found_and_used_as_default(monkeypatch, tmp_path) -> None:
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(bc, "_OS", "Linux")
    monkeypatch.setattr(bc.shutil, "which", lambda b: f"/usr/bin/{b}" if b in ("brave-origin", "omarchy-cmd-default-browser") else None)
    outputs = {"xdg-settings": "omarchy-browser.desktop\n", "omarchy-cmd-default-browser": "brave-origin.desktop\n"}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **_k: SimpleNamespace(stdout=outputs[cmd[0]]))
    assert bc._detect_default_browser() == "brave"
    assert bc._resolve_browser("brave")["exe"] == "/usr/bin/brave-origin"
    (tmp_path / ".config" / "BraveSoftware" / "Brave-Origin").mkdir(parents=True)
    (tmp_path / ".config" / "BraveSoftware" / "Brave-Browser").mkdir(parents=True)
    monkeypatch.setattr(bc.Path, "home", lambda: tmp_path)
    assert bc._real_profile_dir("brave") == str(tmp_path / ".config" / "BraveSoftware" / "Brave-Origin")
