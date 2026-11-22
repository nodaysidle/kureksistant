"""Search queries are URL-encoded, so c++, & and ? reach the engine intact."""
import asyncio

import actions.browser_control as bc

EXPECTED = "https://www.google.com/search?q=c%2B%2B+%26+rust%3F"


def test_native_search_url_is_encoded(monkeypatch) -> None:
    opened: list = []
    monkeypatch.setattr(bc, "_open_native", lambda url, browser: opened.append(url) or f"Opened {url}")
    bc.browser_control({"action": "search", "browser": "firefox", "query": "c++ & rust?"})
    assert opened == [EXPECTED]


def test_session_search_url_is_encoded() -> None:
    seen: list = []

    class _Fake:
        async def go_to(self, url):
            seen.append(url)
            return url

    asyncio.run(bc._BrowserSession.search(_Fake(), "c++ & rust?"))
    assert seen == [EXPECTED]
