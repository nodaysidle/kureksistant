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
